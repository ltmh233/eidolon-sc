"""Production Next and real Go must deliver early refusals without partial JSON."""
import asyncio
from contextlib import asynccontextmanager
import os

import httpx
import pytest

from tests.fakes import FakeClock, FakeLLM
from tests.test_affinity_snapshot import settings as settings
from tests.test_go_stats import gateway_for
from tests.test_sc_play_scope import gameplay_snapshot
from tests.test_three_process import port, ready


@asynccontextmanager
async def next_for(gateway, tmp_path):
    binary = os.environ.get('EIDOLON_NEXT_SERVER')
    if not binary:
        pytest.skip('set EIDOLON_NEXT_SERVER for production proxy acceptance')
    listen = port()
    origin = f'http://127.0.0.1:{listen}'
    with (tmp_path/'next.log').open('wb') as log:
        process = await asyncio.create_subprocess_exec('node',binary,
            env={**os.environ,'HOSTNAME':'127.0.0.1','PORT':str(listen),'BACKEND_URL':str(gateway.base_url).rstrip('/')},
            stdout=log,stderr=log)
        try:
            await ready(origin+'/api/works')
            async with httpx.AsyncClient(base_url=origin,cookies=gateway.cookies,trust_env=False,timeout=5) as client:
                yield client
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(),5)
                except TimeoutError:
                    process.kill()
                    await process.wait()


@pytest.mark.parametrize('hop',['go','next'])
async def test_complete_rejection_json_and_zero_business_writes(make_client,settings,tmp_path,hop):
    clock = FakeClock()
    original = make_client(FakeLLM(),clock=clock)
    created = await original.post('/api/conversations',json={'character_id':'mano'})
    assert created.status_code == 201
    # A rejected request must leave real, initialized SC resources alone, including
    # a pending read-triggered daily grant after the clock advances.
    assert (await original.get('/api/gifts')).status_code == 200
    clock.advance(days=2)
    before = gameplay_snapshot(settings.database_path)
    try:
        async with gateway_for(original,settings,binary_env='EIDOLON_PROXY_BINARY') as (gateway,_):
            if hop == 'go':
                await refused(gateway,created.json()['id'],hop)
            else:
                async with next_for(gateway,tmp_path) as next:
                    await refused(next,created.json()['id'],hop)
    finally:
        assert gameplay_snapshot(settings.database_path) == before, hop+' rejection changed business rows'



async def refused(client,conversation,hop):
    failures = {}
    attempts = 0
    for framing in ('length','chunked'):
        for iteration in range(100):
            for case,body,headers,expected in (
                ('consumed body',b'{"text":"no","work_id":"demo"}',{'Origin':'http://localhost:3000'},409),
                ('gateway scope',b'{"text":"no"}',{'Origin':'http://localhost:3000','X-Work-ID':'demo'},409),
                ('early Origin',b'{"text":"no"}',{'Origin':'https://attacker.invalid'},403),
            ):
                attempts += 1
                async def chunks():
                    yield body
                label = (hop,framing,case,iteration)
                try:
                    response = await client.post('/api/conversations/'+conversation+'/turns',
                        content=body if framing=='length' else chunks(),headers={'Content-Type':'application/json',**headers})
                    assert response.status_code == expected, (label,response.status_code)
                    assert set(response.json()) == {'detail'} and isinstance(response.json()['detail'],str), label
                except httpx.HTTPError as error:
                    key = framing+'/'+case+'/'+type(error).__name__
                    failures[key] = failures.get(key,0)+1
    print('PROXY_REJECTIONS',hop,'attempts',attempts,'failures',failures,flush=True)
    assert not failures, (hop,attempts,failures)


class UploadProbe:
    """Test-only ASGI endpoint; never installed in the production application."""
    def __init__(self,app):
        self.app = app
        self.stopping = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.completed: int | None = None

    async def __call__(self,scope,receive,send):
        if scope['type'] != 'http' or scope['path'] != '/api/proxy-upload':
            return await self.app(scope,receive,send)
        total = 0
        began = False
        while True:
            waiting = asyncio.create_task(receive())
            stopping = asyncio.create_task(self.stopping.wait())
            try:
                done,_ = await asyncio.wait((waiting,stopping),return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    return  # failure cleanup is not reported as a disconnect
                message = waiting.result()
            finally:
                for task in (waiting,stopping):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(waiting,stopping,return_exceptions=True)
            if message['type'] == 'http.disconnect':
                self.disconnected.set()
                return
            total += len(message.get('body',b''))
            if not began and total:
                began = True
                await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
                await send({'type':'http.response.body','body':b'data: first\n\n','more_body':True})
            if not message.get('more_body',False):
                self.completed = total
                await send({'type':'http.response.body','body':f'data: received={total}\n\n'.encode(),'more_body':False})
                return


async def response_chunk(reader):
    line = await reader.readline()
    size = int(line.split(b';',1)[0],16)
    if not size:
        while await reader.readline() not in (b'\r\n',b''):
            pass
        return b''
    chunk = await reader.readexactly(size)
    assert await reader.readexactly(2) == b'\r\n'
    return chunk


async def close_upload(writer):
    """Bound only test cleanup; a peer may have stopped reading buffered upload."""
    writer.close()
    closed = asyncio.create_task(writer.wait_closed())
    try:
        try:
            await asyncio.wait_for(asyncio.shield(closed),1)
        except TimeoutError:
            writer.transport.abort()
            await asyncio.wait_for(closed,1)
    except ConnectionError:
        pass  # closing an already disconnected test peer is still cleanup
    finally:
        if not closed.done():
            writer.transport.abort()
            closed.cancel()
        await asyncio.gather(closed,return_exceptions=True)


async def open_upload(client):
    target = httpx.URL(str(client.base_url))
    reader,writer = await asyncio.open_connection(target.host,target.port)
    cookie = '; '.join(f'{c.name}={c.value}' for c in client.cookies.jar)
    writer.write(('POST /api/proxy-upload HTTP/1.1\r\nHost: localhost\r\nOrigin: http://localhost:3000\r\n'
                  f'Cookie: {cookie}\r\nContent-Type: application/octet-stream\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n').encode())
    await writer.drain()
    try:
        async with asyncio.timeout(2):
            headers = await reader.readuntil(b'\r\n\r\n')
            assert headers.startswith(b'HTTP/1.1 200 ')
            assert b'transfer-encoding: chunked' in headers.lower()
            first = b''
            while b'data: first\n\n' not in first:
                chunk = await response_chunk(reader)
                assert chunk, 'stream ended before first frame'
                first += chunk
        return reader,writer
    except BaseException:
        await close_upload(writer)
        raise


async def test_next_large_upload_streams_both_directions_and_cancels_upstream(make_client,settings,tmp_path):
    import time
    original = make_client(FakeLLM())
    assert isinstance(original._transport,httpx.ASGITransport)
    probe = UploadProbe(original._transport.app)
    async with gateway_for(original,settings,binary_env='EIDOLON_PROXY_BINARY',app_override=probe) as (gateway,_):
        async with next_for(gateway,tmp_path) as next:
            try:
                started = time.monotonic()
                reader,writer = await open_upload(next)
                first_ms = (time.monotonic()-started)*1000
                size = 3 << 20
                try:
                    async with asyncio.timeout(5):
                        # The first SSE frame has already arrived; no bulk bytes or
                        # request terminator have been sent, proving no whole buffering.
                        writer.write(f'{size:x}\r\n'.encode()+b'x'*size+b'\r\n0\r\n\r\n')
                        await writer.drain()
                        tail = b''
                        while chunk := await response_chunk(reader):
                            tail += chunk
                        assert tail == f'data: received={size+1}\n\n'.encode()
                        assert probe.completed == size+1
                finally:
                    await close_upload(writer)
                _,writer = await open_upload(next)
                cancelled = time.monotonic()
                await close_upload(writer)
                await asyncio.wait_for(probe.disconnected.wait(),2)
                print(f'NEXT_UPLOAD first_ms={first_ms:.3f} bytes={size+1} cancel_ms={(time.monotonic()-cancelled)*1000:.3f}',flush=True)
            finally:
                probe.stopping.set()


async def test_upload_failure_cleanup_aborts_a_real_blocked_send_buffer():
    import time
    accepted,release = asyncio.Event(),asyncio.Event()
    peers = []
    async def stopped_reader(_reader,writer):
        peers.append(writer)
        accepted.set()
        try:
            await release.wait()  # deliberately never consume the upload
        finally:
            writer.transport.abort()
    server = await asyncio.start_server(stopped_reader,'127.0.0.1',0)
    assert server.sockets is not None
    writer = None
    pending = None
    try:
        _,writer = await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
        await accepted.wait()
        writer.write(b'x'*(16<<20))
        await asyncio.sleep(0)
        buffered = writer.transport.get_write_buffer_size()
        assert buffered > 0, 'fault never filled the real TCP send buffer'
        writer.close()
        pending = asyncio.create_task(writer.wait_closed())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(pending),.1)
        started = time.monotonic()
        await close_upload(writer)
        elapsed = time.monotonic()-started
        assert elapsed < 3, 'test cleanup hung on an unconsumed upload'
        assert writer.transport.get_write_buffer_size() == 0
        await asyncio.gather(pending,return_exceptions=True)
        print(f'UPLOAD_CLEANUP buffered_bytes={buffered} ordinary_close_timed_out=True bounded_close_ms={elapsed*1000:.3f}',flush=True)
    finally:
        release.set()
        for peer in peers:
            peer.transport.abort()
        if writer is not None:
            writer.transport.abort()
        if pending is not None:
            await asyncio.gather(pending,return_exceptions=True)
        server.close()
        await server.wait_closed()
