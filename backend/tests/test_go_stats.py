"""Real Go -> Python differential statistics, including long imported/deleted history."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import socket
import sqlite3
import time
from typing import cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from tests.fakes import FakeClock, FakeLLM
from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.test_affinity import keep
from tests.test_delete_account import user_id
from tests.test_text_turn import start_conversation
from tests.test_sc_play_scope import gameplay_snapshot


@asynccontextmanager
async def gateway_for(original, settings):
    binary = os.environ.get('EIDOLON_STATS_BINARY')
    if not binary:
        pytest.skip('set EIDOLON_STATS_BINARY for real Go statistics acceptance')
    assert isinstance(original._transport, httpx.ASGITransport)
    app = cast(FastAPI, original._transport.app)
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError('upstream stopped')
                await asyncio.sleep(.01)
        root = Path(__file__).resolve().parents[2]
        process = await asyncio.create_subprocess_exec(
            binary, '-listen', '127.0.0.1:0', '-legacy', f'http://127.0.0.1:{listener.getsockname()[1]}',
            '-packs', f'{root}/core/packs,{root}/core/testdata/packs',
            '-stats-token-file', str(settings.core_service_token_path), stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        ready = await asyncio.wait_for(process.stderr.readline(), 10)
        assert b'catalog listening on ' in ready, ready
        origin = 'http://' + ready.decode().strip().split('catalog listening on ', 1)[1]
        async with httpx.AsyncClient(base_url=origin, trust_env=False, timeout=60,
                                    cookies={c.name: c.value for c in original.cookies.jar if c.value is not None}) as gateway:
            yield gateway, app
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        finally:
            listener.close()


async def test_go_stats_matches_original_across_calendar_and_history(make_client, settings, monkeypatch):
    clock = FakeClock(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))  # NYC repeated 01:30.
    original = make_client(FakeLLM(), clock=clock)
    other = make_client(FakeLLM(), 'other', clock=clock)
    conversation = await start_conversation(original)
    kogane = await start_conversation(original, 'kogane')
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE users SET created_at=? WHERE name='producer'", (clock.now.isoformat(),))
        # Original imports may predate registration, include future records, and span DST.
        times = [clock.now - timedelta(days=n, hours=n % 24) for n in (0, 1, 2, 29, 30, 364, 365, 500)]
        times += [datetime(2026, 3, 8, h, 30, tzinfo=UTC) for h in (6, 7)]
        times += [datetime(2026, 11, 1, h, 30, tzinfo=UTC) for h in (5, 6)]
        times += [clock.now + timedelta(days=2), datetime(1880, 1, 1, tzinfo=UTC)]
        db.executemany("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user',?,?)",
                       [(conversation, 'PRIVATE_TEXT', at.isoformat()) for at in times])
        for c in (conversation, kogane):
            db.execute("INSERT INTO conversation_events(conversation_id,kind,created_at,gift_id,liked) VALUES (?,'gift',?,'cake',1)", (c, clock.now.isoformat()))
            db.execute("INSERT INTO calls(conversation_id,connected_at,duration_s) VALUES (?,?,12.125)", (c, clock.now.isoformat()))
    keep(settings.database_path, 'producer', 0, clock.now - timedelta(hours=12), cooling=True)
    keep(settings.database_path, 'producer', 90, clock.now, character='kogane')
    before_stats = gameplay_snapshot(settings.database_path)
    async with gateway_for(original, settings) as (gateway, app):
        async def compare(label, **kwargs):
            expected = await original.get('/api/stats', **kwargs)
            got = await gateway.get('/api/stats', **kwargs)
            assert got.status_code == expected.status_code, (label, got.text, expected.text)
            assert got.json() == expected.json(), (label, got.text, expected.text)
            assert 'PRIVATE_TEXT' not in got.text, label
            return got.json()

        for zone in ('America/New_York', 'Asia/Tokyo', 'UTC', 'Europe/Amsterdam', 'bad/timezone'):
            for span in ('all', '30d', '1y'):
                await compare((zone, span), headers={'X-Timezone': zone}, params={'range': span})
        assert (await original.put('/api/profile', json={'timezone': 'America/New_York'})).status_code == 200
        await compare('profile overrides header', headers={'X-Timezone': 'Asia/Tokyo'})
        for params in ({'range': 'invalid'}, [('range', 'all'), ('range', '30d')], {'user_id': user_id(settings.database_path, 'other')}):
            await compare(('query', params), params=params)
        for client in (other, original):
            gateway.cookies = {c.name: c.value for c in client.cookies.jar if c.value is not None}
            expected = await client.get('/api/stats')
            assert (await gateway.get('/api/stats')).json() == expected.json(), client
        for params, headers in (({'work_id': 'demo'}, {}), ({}, {'X-Work-ID': 'demo'})):
            denied = await gateway.get('/api/stats', params=params, headers=headers)
            assert denied.status_code == 409, (params, headers, denied.text)
        denied = await gateway.get('/api/_core/stats', headers={'Authorization': 'Bearer ' + TOKEN})
        assert denied.status_code == 404 and 'meta' not in denied.text
        assert gameplay_snapshot(settings.database_path) == before_stats, 'statistics/scope validation wrote gameplay state'
        with monkeypatch.context() as patch:
            patch.setattr(type(app.state.backend.config), 'affinity_on', property(lambda _: False))
            await compare('closed')
        # Model failure injection: the old HTTP route invokes Python aggregation helpers.
        import eidolon.stats
        with monkeypatch.context() as patch:
            def broken(*args, **kwargs):
                raise RuntimeError('public Python aggregation deliberately unavailable')
            patch.setattr(eidolon.stats, 'visits', broken)
            got = await gateway.get('/api/stats')
            assert got.status_code == 200, got.text
        # A removed historical character still participates in the gift maximum.
        retired = await app.state.backend.store.create_conversation(user_id(settings.database_path, 'producer'), 'retired')
        with sqlite3.connect(settings.database_path) as db:
            db.executemany("INSERT INTO conversation_events(conversation_id,kind,created_at,gift_id) VALUES (?,'gift',?,'cake')",
                           [(retired.id, clock.now.isoformat())] * 3)
        removed = await compare('removed character leads gift maximum')
        assert removed['range']['gifts']['most'] is None
        # A representative long history must not hit a total-response byte limit.
        with sqlite3.connect(settings.database_path) as db:
            db.executemany("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user','PRIVATE_TEXT',?)",
                           [(conversation, (clock.now - timedelta(days=i % 1500)).isoformat()) for i in range(50000)])
        started = time.perf_counter()
        large = await compare('50000 imported turns', params={'range': 'all'})
        print(f'PASS stats long-history differential: 50000 extra turns, elapsed={time.perf_counter()-started:.3f}s, total={large["range"]["turns"]}')
        # Remove the retained synthetic records exactly as deletion would remove them.
        with sqlite3.connect(settings.database_path) as db:
            db.execute('DELETE FROM messages WHERE conversation_id=?', (conversation,))
            db.execute('DELETE FROM calls WHERE conversation_id=?', (conversation,))
            db.execute('DELETE FROM conversation_events WHERE conversation_id=?', (conversation,))
        await compare('deleted records disappear')
        gateway.cookies.clear()
        denied = await gateway.get('/api/stats')
        assert denied.status_code == 401 and denied.json() == {'detail': '请先登录'}
        print('PASS Go statistics fixed-clock ranges, timezones/DST, auth/scope, deletion and native calculation')
