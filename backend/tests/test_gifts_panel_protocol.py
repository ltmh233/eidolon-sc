"""Real HTTP/1 chunked controls against Uvicorn, middleware, UserId and SQLite."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import socket
import sqlite3
import time
from typing import Any, Callable

import uvicorn
import pytest

from tests.fakes import FakeLLM
from tests.test_go_gifts_panel import settings as settings, read_settings as read_settings, restore_business  # noqa: F401
from tests.test_delete_account import user_id
from tests.test_sc_play_scope import gameplay_snapshot

TOKEN = '3' * 64
CONTRACT = 'sc-gifts-panel-v1'


@asynccontextmanager
async def upstream_for(client, observer=None, fault=None, cancel_fault=None):
    app = client._transport.app

    async def observed(scope, receive, send):
        async def receiving():
            try:
                message = await receive()
                if cancel_fault is not None and message['type'] == 'http.disconnect':
                    await asyncio.Event().wait()
            except asyncio.CancelledError:
                if cancel_fault is None or cancel_fault.is_set():
                    raise
                # Model the observed AnyIO cancellation-scope race once, at the
                # actual ASGI receive boundary. This never executes business work.
                cancel_fault.set()
                message = await receive()
            if fault is not None and fault['armed'] and message['type'] == 'http.request':
                fault['armed'] = False
                fault['hit'].set()
                raise RuntimeError('test-only actual ASGI receive failure')
            if observer is not None and message['type'] == 'http.disconnect':
                observer.set()
            return message
        await app(scope, receiving, send)

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(observed, lifespan='off', log_level='error', http='httptools'))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(.01)
        yield listener.getsockname()[1]
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


class Wire:
    """A test peer that explicitly controls request EOF independently of response reads."""
    def __init__(self, reader, writer):
        self.reader, self.writer, self.buffer = reader, writer, b''
        self.status, self.headers = 0, b''
        self.request_ended = False
        self.record_sizes = {}
        self.on_record: Callable[[Any, int], None] | None = None

    @classmethod
    async def open(cls, port, client, *, token=TOKEN, headers=(), host='127.0.0.1'):
        reader, writer = await asyncio.open_connection(host, port)
        wire = cls(reader, writer)
        cookie = '; '.join(f'{c.name}={c.value}' for c in client.cookies.jar)
        data = (f'GET /api/_core/sc-gifts-panel HTTP/1.1\r\nHost: localhost\r\n'
                f'Authorization: Bearer {token}\r\nCookie: {cookie}\r\n'
                'Transfer-Encoding: chunked\r\nConnection: close\r\n'
                + ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n')
        writer.write(data.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
        wire.status = int(raw.split(b' ')[1])
        wire.headers = raw
        return wire

    async def raw(self, body):
        self.writer.write(f'{len(body):x}\r\n'.encode() + body + b'\r\n')
        await self.writer.drain()

    async def write(self, row):
        await self.raw(json.dumps(row, separators=(',', ':')).encode() + b'\n')

    async def eof(self):
        assert not self.request_ended
        self.writer.write(b'0\r\n\r\n')
        await self.writer.drain()
        self.request_ended = True

    async def row(self) -> Any:
        async with asyncio.timeout(15):
            while b'\n' not in self.buffer:
                size = int((await self.reader.readline()).split(b';', 1)[0], 16)
                if size == 0:
                    assert await self.reader.readline() == b'\r\n'
                    return None
                self.buffer += await self.reader.readexactly(size)
                assert await self.reader.readexactly(2) == b'\r\n'
            line, self.buffer = self.buffer.split(b'\n', 1)
            row = json.loads(line)
            if isinstance(row, list) and row and isinstance(row[0], str):
                counts = self.record_sizes.setdefault(row[0], [0, 0])
                counts[0] += 1
                counts[1] += len(line) + 1
            if self.on_record is not None:
                self.on_record(row, len(line) + 1)
            return row

    async def phase(self):
        rows = []
        while True:
            row = await self.row()
            rows.append(row)
            if row[0] == 'ready':
                return rows

    async def begin(self):
        await self.write(['begin', CONTRACT])
        assert await self.row() == ['hello', CONTRACT, 'sc', 'http1-duplex-v1']
        return await self.phase()

    async def next_all(self):
        daily = await self.begin()
        await self.write(['next', 1, daily[0][2]])
        return await self.phase()

    async def end(self):
        if not self.request_ended:
            await self.eof()
        assert await self.row() == ['end']
        assert await self.row() is None
        assert await asyncio.wait_for(self.reader.read(), 2) == b''

    async def close(self):
        self.writer.close()
        await self.writer.wait_closed()


async def test_panel_wire_errors_never_reach_state_before_authorization(make_client, settings):
    client = make_client(FakeLLM())
    before = gameplay_snapshot(settings.database_path)
    async with upstream_for(client) as port:
        for scenario in ('extra identity', 'blank', 'bad utf8', 'lone surrogate', 'nan', 'long line', 'queue overflow',
                         'phase bool', 'wrong nonce', 'skip phase', 'abort extra bytes', 'early eof'):
            wire = await Wire.open(port, client)
            try:
                if scenario == 'extra identity':
                    await wire.write(['begin', CONTRACT, 'producer'])
                elif scenario in ('blank', 'bad utf8', 'lone surrogate', 'nan', 'long line', 'queue overflow'):
                    payload = {'blank': b'\n', 'bad utf8': b'["begin","\xff"]\n',
                               'lone surrogate': b'["begin","\\ud800"]\n', 'nan': b'[NaN]\n',
                               'long line': b' ' * 257 + b'\n', 'queue overflow': b'[]\n[]\n[]\n'}[scenario]
                    await wire.raw(payload)
                else:
                    daily = await wire.begin()
                    nonce = daily[0][2]
                    if scenario == 'early eof':
                        await wire.eof()
                    elif scenario == 'abort extra bytes':
                        await wire.write(['abort', 1, nonce, 'legacy'])
                        await wire.write(['next', 1, nonce])
                    else:
                        row = {'phase bool': ['next', True, nonce], 'wrong nonce': ['next', 1, '0' * 32],
                               'skip phase': ['next', 2, nonce]}[scenario]
                        await wire.write(row)
                error = await wire.row()
                # A malformed begin may fail before hello; a pump error may race it.
                if error and error[0] == 'hello':
                    error = await wire.row()
                assert error[0] == 'error' and error[2] == 'protocol', (scenario, error)
                await wire.end()
                assert gameplay_snapshot(settings.database_path) == before, ('phase=pre-state', scenario)
            finally:
                await wire.close()


async def test_panel_valid_abort_foreign_nonce_and_duplicate_next2(make_client, settings, monkeypatch):
    client = make_client(FakeLLM())
    backend = client._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    calls = []
    original = backend.gifts.state

    async def state(*args):
        calls.append(args)
        return await original(*args)

    monkeypatch.setattr(backend.gifts, 'state', state)
    async with upstream_for(client) as port:
        for phase in (1, 2):
            wire = await Wire.open(port, client)
            rows = await (wire.begin() if phase == 1 else wire.next_all())
            await wire.write(['abort', phase, rows[0][2], 'legacy'])
            await wire.eof()
            await wire.end()
            await wire.close()
            assert not calls and gameplay_snapshot(settings.database_path) == before, ('phase=abort', phase, 'scope=sc user=producer')
        a, b = await Wire.open(port, client), await Wire.open(port, client)
        try:
            ar, br = await a.next_all(), await b.next_all()
            assert ar[0][2] != br[0][2]
            await a.write(['next', 2, br[0][2]])
            assert (await a.row())[2] == 'protocol'
            await a.end()
            assert not calls and gameplay_snapshot(settings.database_path) == before
        finally:
            await a.close()
            await b.close()
        wire = await Wire.open(port, client)
        rows = await wire.next_all()
        await wire.write(['next', 2, rows[0][2]])
        state_row = await wire.row()
        assert state_row[0] == 'state'
        await wire.write(['next', 2, rows[0][2]])
        assert (await wire.row())[2] == 'protocol'
        await wire.end()
        await wire.close()
        assert len(calls) == 1
        after = gameplay_snapshot(settings.database_path)
        assert len(after['gift_balances']) == 1 and after['gift_balances'][0][1] == 1
        assert after['gift_rewards'] == before['gift_rewards']


async def test_panel_disconnect_cancels_owner_but_real_queued_writer_commits(make_client, settings, monkeypatch):
    import threading

    client = make_client(FakeLLM())
    backend = client._transport.app.state.backend
    entered, release = threading.Event(), threading.Event()
    queued, disconnected, canceled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    original_submit, original_state = backend.database._submit, backend.gifts.state
    calls = []

    def block(db):
        entered.set()
        assert release.wait(5), 'test must release real writer'

    def submit(body, args, **kwargs):
        future = original_submit(body, args, **kwargs)
        if getattr(body, '__name__', '') == '_state' and args[-1] is True:
            loop.call_soon_threadsafe(queued.set)
        return future

    async def state(*args):
        calls.append(args)
        try:
            return await original_state(*args)
        except asyncio.CancelledError:
            canceled.set()
            raise

    monkeypatch.setattr(backend.database, '_submit', submit)
    monkeypatch.setattr(backend.gifts, 'state', state)
    blocker = original_submit(block, ())
    assert await asyncio.to_thread(entered.wait, 2)
    try:
        async with upstream_for(client, disconnected) as port:
            wire = await Wire.open(port, client)
            rows = await wire.next_all()
            await wire.write(['next', 2, rows[0][2]])
            await asyncio.wait_for(queued.wait(), 2)
            with sqlite3.connect(settings.database_path) as db:
                assert db.execute('SELECT * FROM gift_balances').fetchall() == []
            await wire.close()
            await asyncio.wait_for(disconnected.wait(), 2)
            await asyncio.wait_for(canceled.wait(), 2)
            release.set()
            await asyncio.wrap_future(blocker)
            await backend.database.write(lambda db: None)  # drains the actual queued state transaction
            after = gameplay_snapshot(settings.database_path)
            assert len(calls) == 1 and after['gift_balances'][0][1] == 1
            assert after['gift_rewards'] == []
    finally:
        release.set()


async def test_panel_eight_slots_idle_timeout_and_recovery(make_client, settings):
    client = make_client(FakeLLM())
    before = gameplay_snapshot(settings.database_path)
    async with upstream_for(client) as port:
        wires = [await Wire.open(port, client) for _ in range(8)]
        try:
            denied = await Wire.open(port, client)
            assert denied.status == 503
            await denied.close()
            started = time.monotonic()
            results = await asyncio.gather(*(wire.row() for wire in wires))
            elapsed = time.monotonic() - started
            assert 9 <= elapsed <= 12, elapsed
            assert all(r == ['error', 0, 'timeout'] for r in results), results
            await asyncio.gather(*(wire.end() for wire in wires))
            recovered = await Wire.open(port, client)
            assert recovered.status == 200
            daily = await recovered.begin()
            await recovered.write(['abort', 1, daily[0][2], 'legacy'])
            await recovered.eof()
            await recovered.end()
            await recovered.close()
            assert gameplay_snapshot(settings.database_path) == before
        finally:
            await asyncio.gather(*(wire.close() for wire in wires))


@pytest.mark.timeout(75)
async def test_panel_absolute_sixty_second_deadline_cancels_inflight_read(make_client, settings, monkeypatch):
    client = make_client(FakeLLM())
    backend = client._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    read = backend.database.read
    entered, canceled = asyncio.Event(), asyncio.Event()
    never = asyncio.Event()

    async def held(body, *args):
        result = await read(body, *args)
        if getattr(body, '__name__', '') == '_counts':
            entered.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                canceled.set()
                raise
        return result

    monkeypatch.setattr(backend.database, 'read', held)
    async with upstream_for(client) as port:
        wire = await Wire.open(port, client)
        started = time.monotonic()
        await wire.write(['begin', CONTRACT])
        assert (await wire.row())[0] == 'hello'
        await asyncio.wait_for(entered.wait(), 2)
        try:
            # The original SQL read already completed; its dialog owner is held in
            # business work, so the ten-second control idle timer does not apply.
            remainder = await asyncio.wait_for(wire.reader.read(), 65)
            elapsed = time.monotonic() - started
            assert 59 <= elapsed <= 63, elapsed
            assert b'"state"' not in remainder
            assert canceled.is_set()
            assert gameplay_snapshot(settings.database_path) == before
            print(f'PASS absolute real monotonic deadline {elapsed:.3f}s; SQL read canceled, no state write')
        finally:
            await wire.close()


async def test_panel_capability_scope_and_single_user_dependency(make_client, settings, monkeypatch):
    client = make_client(FakeLLM())
    backend = client._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    use = backend.store.use_session
    uses = []

    async def traced(*args):
        uses.append(args)
        return await use(*args)

    monkeypatch.setattr(backend.store, 'use_session', traced)
    path = '/api/_core/sc-gifts-panel'
    for headers in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'Bearer ' + '1' * 64},
                    {'Authorization': 'Bearer ' + '2' * 64},
                    [('Authorization', 'Bearer ' + TOKEN), ('Authorization', 'Bearer ' + TOKEN)]):
        response = await client.get(path, headers=headers)
        assert response.status_code == 404 and not uses, (headers, response.text)
    for params, headers in (({'work_id': 'demo'}, {}), ([('work_id', 'sc'), ('work_id', 'demo')], {}),
                            ({}, [('X-Work-ID', 'sc'), ('X-Work-ID', 'demo')])):
        auth = [('Authorization', 'Bearer ' + TOKEN), *list(headers.items() if isinstance(headers, dict) else headers)]
        response = await client.get(path, params=params, headers=auth)
        assert response.status_code == 409 and not uses, (params, headers, response.text)
    for route in ('/api/_core/affinity/mano', '/api/_core/achievements/mano'):
        response = await client.get(route, headers={'Authorization': 'Bearer ' + TOKEN})
        assert response.status_code == 404 and not uses, ('phase=capability', route, 'scope=sc user=producer')
    async with upstream_for(client) as port:
        wire = await Wire.open(port, client, headers=(('Origin', 'https://foreign.example'),))
        daily = await wire.begin()
        await wire.write(['abort', 1, daily[0][2], 'legacy'])
        await wire.eof()
        await wire.end()
        await wire.close()
    assert len(uses) == 1 and gameplay_snapshot(settings.database_path) == before


async def test_panel_inflight_identity_is_not_reauthenticated(make_client, settings, monkeypatch):
    client = make_client(FakeLLM())
    backend = client._transport.app.state.backend
    uid = user_id(settings.database_path, 'producer')
    before = gameplay_snapshot(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        sessions = db.execute('SELECT * FROM sessions').fetchall()
        consented = db.execute('SELECT consented_at FROM users WHERE id=?', (uid,)).fetchone()[0]
    use = backend.store.use_session
    uses = []

    async def traced(*args):
        uses.append(args)
        return await use(*args)

    monkeypatch.setattr(backend.store, 'use_session', traced)
    async with upstream_for(client) as port:
        for action, next_status in (('signout', 401), ('expire', 401), ('ban', 403), ('consent', 428), ('delete', 401)):
            restore_business(settings.database_path, before)
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE users SET banned_at=NULL,consented_at=? WHERE id=?', (consented, uid))
                db.execute('DELETE FROM sessions')
                db.executemany('INSERT INTO sessions VALUES (?,?,?,?)', sessions)
            uses.clear()
            wire = await Wire.open(port, client)
            daily = await wire.begin()
            assert len(uses) == 1, action
            if action == 'delete':
                await backend.store.delete_user(uid)
            else:
                with sqlite3.connect(settings.database_path) as db:
                    if action == 'signout':
                        db.execute('DELETE FROM sessions')
                    elif action == 'expire':
                        db.execute('UPDATE sessions SET expires_at=0')
                    elif action == 'ban':
                        db.execute('UPDATE users SET banned_at=? WHERE id=?', (backend.clock().isoformat(), uid))
                    else:
                        db.execute('UPDATE users SET consented_at=NULL WHERE id=?', (uid,))
            await wire.write(['next', 1, daily[0][2]])
            all_rows = await wire.phase()
            await wire.write(['next', 2, all_rows[0][2]])
            state = await wire.row()
            if action == 'delete':
                assert state == ['error', 3, 'legacy'], (action, state)
            else:
                assert state[0] == 'state', (action, state)
                await wire.write(['finish', 3, state[2], 'ok'])
                await wire.eof()
            await wire.end()
            await wire.close()
            assert len(uses) == 1, ('inflight identity must not run UserId again', action)
            denied = await client.get('/api/_core/sc-gifts-panel', headers={'Authorization': 'Bearer ' + TOKEN})
            assert denied.status_code == next_status, (action, denied.text)


async def test_panel_receive_failure_releases_real_admission(make_client, settings):
    client = make_client(FakeLLM())
    before = gameplay_snapshot(settings.database_path)
    fault = {'armed': True, 'hit': asyncio.Event()}
    async with upstream_for(client, fault=fault) as port:
        failed = await Wire.open(port, client)
        try:
            await failed.write(['begin', CONTRACT])
            await asyncio.wait_for(fault['hit'].wait(), 2)
            assert await failed.row() == ['error', 0, 'internal']
            await failed.eof()
            remainder = await asyncio.wait_for(failed.reader.read(), 2)
            assert b'["end"]' not in failed.buffer + remainder, 'phase=receive-failure: no complete end without known EOF'
        finally:
            await failed.close()
        # The failed receiver cannot leave a ninth phantom lease behind. This
        # proves admission through real authenticated HTTP responses, independently
        # of the implementation's release callback or cleanup instrumentation.
        wires = [await Wire.open(port, client) for _ in range(8)]
        try:
            assert all(w.status == 200 for w in wires)
            ninth = await Wire.open(port, client)
            assert ninth.status == 503
            await ninth.close()
            assert gameplay_snapshot(settings.database_path) == before
        finally:
            await asyncio.gather(*(w.close() for w in wires))


async def test_panel_cleanup_completes_response_before_joining_cancel_scope(make_client, settings, caplog):
    caplog.set_level(logging.INFO)
    client = make_client(FakeLLM())
    uid = user_id(settings.database_path, 'producer')
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', (uid, 'old', '', 1, 'bad'))
    before = gameplay_snapshot(settings.database_path)
    swallowed = asyncio.Event()
    async with upstream_for(client, cancel_fault=swallowed) as port:
        wire = await Wire.open(port, client)
        try:
            daily = await wire.begin()
            await wire.write(['next', 1, daily[0][2]])
            assert await wire.row() == ['error', 2, 'legacy']
            await wire.eof()
            # A conforming error peer closes its writer before validating the end.
            # The receiver still absorbs the first cancellation at a scope boundary.
            await wire.end()
            await asyncio.wait_for(swallowed.wait(), 2)
            assert not [r for r in caplog.records if r.name == 'eidolon.gifts_panel' and r.levelno >= logging.ERROR
                        and 'cleanup' in r.message], caplog.text
            assert gameplay_snapshot(settings.database_path) == before
        finally:
            await wire.close()


async def test_panel_non_eof_error_peer_exhausts_only_closing_budget_and_releases_slots(make_client, settings):
    client = make_client(FakeLLM())
    uid = user_id(settings.database_path, 'producer')
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', (uid, 'old', '', 1, 'bad'))
    before = gameplay_snapshot(settings.database_path)
    async with upstream_for(client) as port:
        wire = await Wire.open(port, client)
        try:
            daily = await wire.begin()
            await wire.write(['next', 1, daily[0][2]])
            assert await wire.row() == ['error', 2, 'legacy']
            started = time.monotonic()
            # Deliberately do not close the control writer. This peer cannot require
            # a complete HTTP EOF; it must still release the dialog within budget.
            remainder = await asyncio.wait_for(wire.reader.read(), 2)
            assert b'["end"]' not in wire.buffer + remainder, 'phase=closing: non-EOF peer has no complete legacy termination'
            assert time.monotonic() - started <= 1.5, 'phase=closing scope=sc non-EOF peer'
        finally:
            await wire.close()
        wires = [await Wire.open(port, client) for _ in range(8)]
        try:
            assert all(w.status == 200 for w in wires), 'phase=admission after non-EOF error peer'
            assert gameplay_snapshot(settings.database_path) == before
        finally:
            await asyncio.gather(*(w.close() for w in wires))


async def test_panel_rejection_closing_faults_never_authenticate_or_hold_slots(make_client, settings, monkeypatch):
    client = make_client(FakeLLM())
    backend = client._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    use = backend.store.use_session
    uses = []

    async def traced(*args):
        uses.append(args)
        return await use(*args)

    monkeypatch.setattr(backend.store, 'use_session', traced)
    for scenario in ('no EOF', 'oversize', 'receive failure'):
        fault = {'armed': True, 'hit': asyncio.Event()} if scenario == 'receive failure' else None
        async with upstream_for(client, fault=fault) as port:
            wire = await Wire.open(port, client, token='wrong')
            started = time.monotonic()
            try:
                assert wire.status == 404, ('phase=credential rejection', scenario)
                if scenario == 'oversize':
                    await wire.raw(b'x' * 1025)
                if scenario == 'receive failure':
                    assert fault is not None, ('phase=rejection receive fault', scenario)
                    await wire.write(['begin', CONTRACT])
                    await asyncio.wait_for(fault['hit'].wait(), 2)
                await asyncio.wait_for(wire.reader.read(), 2)
                assert time.monotonic() - started <= 1.5, ('phase=rejection closing', scenario)
                assert not uses and gameplay_snapshot(settings.database_path) == before, ('phase=credential before UserId', scenario)
            finally:
                await wire.close()
            wires = [await Wire.open(port, client) for _ in range(8)]
            try:
                assert all(w.status == 200 for w in wires), ('phase=admission after rejection closing', scenario)
            finally:
                await asyncio.gather(*(w.close() for w in wires))
            uses.clear()
