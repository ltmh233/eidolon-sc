"""Real Go command -> HTTP -> FastAPI -> synthetic SQLite, including response loss."""
import asyncio
from contextlib import asynccontextmanager
import sqlite3
from typing import cast

import httpx
from fastapi import FastAPI

from tests.fakes import FakeClock, FakeLLM
from tests.test_sc_create_command import settings as settings
from tests.test_go_stats import gateway_for
from tests.test_sc_play_scope import gameplay_snapshot


@asynccontextmanager
async def creating(client, settings, *, app_override=None):
    async with gateway_for(client, settings, binary_env='EIDOLON_CREATE_BINARY',
                           token_flag='-sc-create-token-file', token_file=settings.core_sc_create_token_path,
                           extra_args=('-content-snapshot', str(settings.character_snapshot_path)),
                           app_override=app_override) as result:
        yield result


async def test_native_create_compatibility_scopes_and_renewal(make_client, settings):
    clock = FakeClock()
    original = make_client(FakeLLM(), clock=clock)
    async with creating(original, settings) as (gateway, app):
        # Each successful create is a new intent; five-field public shape is unchanged.
        identities = []
        for label, body in (('omitted',{'character_id':'mano','unknown':'kept'}),
                            ('explicit SC',{'character_id':'mano','work_id':'sc'})):
            clock.advance(hours=2)
            response = await gateway.post('/api/conversations', json=body, headers={'Origin':'http://localhost:3000'})
            assert response.status_code == 201, (label,response.text)
            assert len(response.headers.get_list('set-cookie')) == 1, (label,response.headers)
            row = response.json()
            assert set(row) == {'id','user_id','character_id','created_at','title'}, label
            assert row['character_id'] == 'mano' and row['title'] is None, label
            assert len(row['id']) == 32 and row['id'][12] == '4', label
            identities.append(row['id'])
        assert identities[0] != identities[1]
        # prepare renewed, but Go's own admission returns 404; do not lose renewal.
        clock.advance(hours=2)
        before = gameplay_snapshot(settings.database_path)
        missing = await gateway.post('/api/conversations', json={'character_id':'missing'}, headers={'Origin':'http://localhost:3000'})
        assert missing.status_code == 404 and missing.json() == {'detail':'角色不存在'}
        assert len(missing.headers.get_list('set-cookie')) == 1
        assert gameplay_snapshot(settings.database_path) == before
        cases = [({'character_id':'mano','work_id':work},{},[]) for work in ('demo',None,'SC',' sc ',1,False,[],{})]
        cases += [({'character_id':'mano'}, {'X-Work-ID':'demo'}, []),
                  ({'character_id':'mano'}, {}, [('work_id','sc'),('work_id','demo')]),
                  ({'character_id':'mano','work_id':'demo'}, {'X-Work-ID':'sc'}, []),
                  ({'character_id':'mano','requestData':{'work_id':None}}, {}, [])]
        for body, headers, params in cases:
            for attempt in range(2):
                denied = await gateway.post('/api/conversations', json=body, params=params,
                                           headers={'Origin':'http://localhost:3000', **headers})
                assert denied.status_code == 409, ('gateway', body,headers,params,attempt,denied.text)
                assert gameplay_snapshot(settings.database_path) == before, ('gateway',body,headers,params,attempt)
        duplicate = await gateway.post('/api/conversations', content='{"character_id":"mano","work_id":"demo","work_id":"sc"}',
                                      headers={'Origin':'http://localhost:3000','Content-Type':'application/json'})
        assert duplicate.status_code == 409, ('gateway',duplicate.text)
        assert gameplay_snapshot(settings.database_path) == before
        # Membership really belongs to Go: prepare alone doesn't approve a removed member.
        app.state.backend.characters.pop('mano')
        refused = await gateway.post('/api/conversations', json={'character_id':'mano'}, headers={'Origin':'http://localhost:3000'})
        assert refused.status_code == 502 and '暂未确认' in refused.text
        assert gameplay_snapshot(settings.database_path) == before
    print('PASS native creation: compatible shape/new intents, real renewal on success/404, SC scope zero business writes')


class LostCommittedResponse:
    """Drop the actual committed execute reply before HTTP response headers."""
    def __init__(self, app):
        self.app = app
        self.attempts = 0
        self.committed = asyncio.Event()
        self.hold_prepare = False
        self.prepared = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, scope, receive, send):
        path = scope.get('path','')
        if path.endswith('/execute'):
            self.attempts += 1
            async def lose(message):
                if message['type'] == 'http.response.start':
                    assert message['status'] == 201, message
                    self.committed.set()
                    raise RuntimeError('test-only: committed response lost')
            await self.app(scope,receive,lose)
        elif self.hold_prepare and path.endswith('/prepare'):
            async def hold(message):
                if message['type'] == 'http.response.start':
                    assert message['status'] == 200
                    self.prepared.set()
                    await self.release.wait()
                await send(message)
            await self.app(scope,receive,hold)
        else:
            await self.app(scope,receive,send)


async def test_real_committed_response_loss_does_not_retry_or_fallback(make_client, settings):
    original = make_client(FakeLLM())
    app = cast(FastAPI, original._transport.app)
    fault = LostCommittedResponse(app)
    async with creating(original, settings, app_override=fault) as (gateway, _):
        before = gameplay_snapshot(settings.database_path)
        response = await gateway.post('/api/conversations',json={'character_id':'mano'},headers={'Origin':'http://localhost:3000'})
        await asyncio.wait_for(fault.committed.wait(), 2)
        assert response.status_code == 502 and '暂未确认' in response.text, response.text
        assert fault.attempts == 1
        after = gameplay_snapshot(settings.database_path)
        assert len(after.pop('conversations')) == 1
        before.pop('conversations')
        assert after == before
        assert len((await original.get('/api/conversations',params={'character_id':'mano'})).json()) == 1
    print('PASS injected after actual COMMIT: one execute / one stored row / unknown result, no fallback')


async def test_public_disconnect_before_execute_never_enqueues_creation(make_client, settings):
    original = make_client(FakeLLM())
    fault = LostCommittedResponse(cast(FastAPI,original._transport.app))
    fault.hold_prepare = True
    async with creating(original,settings,app_override=fault) as (gateway,_):
        before = gameplay_snapshot(settings.database_path)
        # Real TCP connection, not a mocked request cancellation.
        target = httpx.URL(str(gateway.base_url))
        reader, writer = await asyncio.open_connection(target.host,target.port)
        body = b'{"character_id":"mano"}'
        cookie = '; '.join(f'{c.name}={c.value}' for c in gateway.cookies.jar)
        writer.write(f'POST /api/conversations HTTP/1.1\r\nHost: localhost\r\nOrigin: http://localhost:3000\r\nCookie: {cookie}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body)
        await writer.drain()
        try:
            await asyncio.wait_for(fault.prepared.wait(),2)
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(reader.read(),2)
            fault.release.set()
            # A subsequent real request is an observable round-trip barrier.
            assert (await gateway.get('/api/works')).status_code == 200
            await asyncio.sleep(.1)
            assert fault.attempts == 0
            assert gameplay_snapshot(settings.database_path) == before
        finally:
            fault.release.set()
            writer.close()
            await writer.wait_closed()
    print('PASS real client disconnect while prepare held: zero execute / zero conversation writes')


async def test_insert_trigger_failure_rolls_back_without_new_id_or_retry(make_client, settings):
    original = make_client(FakeLLM())
    async with creating(original,settings) as (gateway,_):
        with sqlite3.connect(settings.database_path) as db:
            db.execute("CREATE TRIGGER test_abort_create AFTER INSERT ON conversations BEGIN SELECT RAISE(ABORT,'injected failure after INSERT'); END")
        before = gameplay_snapshot(settings.database_path)
        failed = await gateway.post('/api/conversations',json={'character_id':'mano'},headers={'Origin':'http://localhost:3000'})
        assert failed.status_code == 502, failed.text
        assert gameplay_snapshot(settings.database_path) == before
    print('PASS actual SQLite AFTER INSERT failure: rollback, no partial row or regenerated ID')


async def test_disconnect_during_actual_insert_does_not_retract_queued_write(make_client, settings):
    import threading
    original = make_client(FakeLLM())
    app = cast(FastAPI,original._transport.app)
    reached, release = threading.Event(), threading.Event()
    def pause_insert():
        reached.set()
        if not release.wait(5):
            raise RuntimeError('test release timeout')
        return 1
    await asyncio.to_thread(app.state.backend.database.function,'test_pause_insert',0,pause_insert)
    with sqlite3.connect(settings.database_path) as db:
        db.execute('CREATE TRIGGER test_pause AFTER INSERT ON conversations BEGIN SELECT test_pause_insert(); END')
    async with creating(original,settings) as (gateway,_):
        before = gameplay_snapshot(settings.database_path)
        request = asyncio.create_task(gateway.post('/api/conversations',json={'character_id':'mano'},headers={'Origin':'http://localhost:3000'}))
        try:
            assert await asyncio.to_thread(reached.wait,2), 'fault did not reach actual INSERT'
            request.cancel()
            try:
                await request
            except asyncio.CancelledError:
                pass
            release.set()
            async with asyncio.timeout(3):
                while True:
                    listed = await original.get('/api/conversations',params={'character_id':'mano'})
                    if len(listed.json()) == 1:
                        break
                    await asyncio.sleep(.01)
            after = gameplay_snapshot(settings.database_path)
            assert len(after.pop('conversations')) == 1
            before.pop('conversations')
            assert after == before
        finally:
            release.set()
            if not request.done():
                request.cancel()
                await asyncio.gather(request,return_exceptions=True)
    print('PASS cancel after real INSERT before COMMIT: queued task still commits one row')


async def test_native_auth_origin_and_conflicting_headers_are_not_bypassed(make_client, settings):
    original = make_client(FakeLLM())
    async with creating(original,settings) as (gateway,_):
        before = gameplay_snapshot(settings.database_path)
        for label, headers, expected in (
            ('missing Origin',{},403), ('foreign Origin',{'Origin':'https://evil.invalid'},403),
            ('duplicate header',[('Origin','http://localhost:3000'),('X-Work-ID','demo'),('X-Work-ID','sc')],409),
        ):
            response = await gateway.post('/api/conversations',json={'character_id':'mano'},headers=headers)
            assert response.status_code == expected, (label,response.text)
            assert gameplay_snapshot(settings.database_path) == before, label
        gateway.cookies.clear()
        denied = await gateway.post('/api/conversations',json={'character_id':'mano'},headers={'Origin':'http://localhost:3000','X-User-ID':'producer'})
        assert denied.status_code == 401
        assert gameplay_snapshot(settings.database_path) == before


async def test_native_preserves_auth_and_model_error_precedence(make_client, settings):
    for identity in ('producer', None):
        original = make_client(FakeLLM(), user=identity)
        async with creating(original,settings) as (gateway,_):
            for label, body in (('missing',{}), ('bad type',{'character_id':123}), ('not object',[]),
                                ('unknown',{'character_id':'missing'}), ('legal',{'character_id':'mano'})):
                if identity is not None and label == 'legal':
                    continue  # Successful intentions have their own persistence tests.
                expected = await original.post('/api/conversations',json=body)
                actual = await gateway.post('/api/conversations',json=body,headers={'Origin':'http://localhost:3000'})
                assert actual.status_code == expected.status_code and actual.json() == expected.json(), (identity,label,actual.text,expected.text)
            for raw in ('{bad json', 'null', b'\xff'):
                expected = await original.post('/api/conversations',content=raw,headers={'Content-Type':'application/json'})
                actual = await gateway.post('/api/conversations',content=raw,headers={'Content-Type':'application/json','Origin':'http://localhost:3000'})
                assert actual.status_code == expected.status_code and actual.json() == expected.json(), (identity,raw,actual.text,expected.text)


def test_native_create_cli_requires_v3_and_distinct_read_credentials(settings, tmp_path):
    import os
    import subprocess
    from tests.test_character_snapshot import ROOT, CONFIG, export
    binary = os.environ.get('EIDOLON_CREATE_BINARY')
    if not binary:
        import pytest
        pytest.skip('set EIDOLON_CREATE_BINARY for real CLI acceptance')
    v2 = tmp_path / 'v2.json'
    assert export(CONFIG,v2).returncode == 0
    base = [binary,'-listen','127.0.0.1:0','-packs',f'{ROOT}/core/packs,{ROOT}/core/testdata/packs',
            '-sc-create-token-file',str(settings.core_sc_create_token_path)]
    for label, args, message in (
        ('missing snapshot', [], b'requires v3'),
        ('v2 snapshot', ['-content-snapshot',str(v2)], b'requires v3'),
        ('read equals create', ['-content-snapshot',str(settings.character_snapshot_path),
                               '-summaries-token-file',str(settings.core_sc_create_token_path)], b'must be distinct'),
    ):
        result = subprocess.run(base+args,timeout=10,capture_output=True)
        assert result.returncode != 0 and message in result.stderr, (label,result.stderr)


async def test_native_keeps_original_large_request_validation_results(make_client, settings):
    original = make_client(FakeLLM())
    async with creating(original,settings) as (gateway,_):
        before = gameplay_snapshot(settings.database_path)
        for label,body in (('large unknown character',{'character_id':'x'*70000}),
                           ('large invalid type',{'character_id':['x'*70000]})):
            expected = await original.post('/api/conversations',json=body)
            actual = await gateway.post('/api/conversations',json=body,headers={'Origin':'http://localhost:3000'})
            assert actual.status_code == expected.status_code and actual.json() == expected.json(), (label,actual.status_code,expected.status_code)
            assert gameplay_snapshot(settings.database_path) == before,label


async def test_concurrent_public_posts_are_two_independent_intents(make_client, settings):
    original = make_client(FakeLLM())
    async with creating(original,settings) as (gateway,_):
        before = gameplay_snapshot(settings.database_path)
        results = await asyncio.gather(*(gateway.post('/api/conversations',json={'character_id':'mano'},headers={'Origin':'http://localhost:3000'}) for _ in range(2)))
        for attempt,response in enumerate(results):
            assert response.status_code == 201, (attempt,response.text)
        assert results[0].json()['id'] != results[1].json()['id']
        after = gameplay_snapshot(settings.database_path)
        assert len(after.pop('conversations')) == 2
        before.pop('conversations')
        assert after == before


async def test_account_deleted_after_reauthentication_cannot_gain_orphan_conversation(make_client, settings, monkeypatch):
    original = make_client(FakeLLM())
    async with creating(original,settings) as (gateway,app):
        store = app.state.backend.store
        create = store.create_conversation
        deleted = asyncio.Event()
        async def delete_after_auth(user_id,character_id,**kwargs):
            await store.delete_user(user_id)
            deleted.set()
            return await create(user_id,character_id,**kwargs)
        monkeypatch.setattr(store,'create_conversation',delete_after_auth)
        response = await gateway.post('/api/conversations',json={'character_id':'mano'},headers={'Origin':'http://localhost:3000'})
        assert deleted.is_set(), 'fault did not reach authenticated write boundary'
        assert response.status_code == 502 and '暂未确认' in response.text
        with sqlite3.connect(settings.database_path) as db:
            assert db.execute('SELECT count(*) FROM conversations').fetchone() == (0,)
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
