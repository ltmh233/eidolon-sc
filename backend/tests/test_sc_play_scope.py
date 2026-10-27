"""SC gameplay HTTP scope is rejected before even read-triggered rewards are written."""
import sqlite3

import pytest

from tests.fakes import FakeClock
from tests.test_gift_giving import (
    CAKE, backpack, catalog as catalog, draw_them, holding, settings as settings,
)


def gameplay_snapshot(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                for table in (
                    'gift_balances', 'gift_backpacks', 'gift_rewards', 'gifts_opened',
                    'reset_card_grants', 'affinities', 'daily_task_progress',
                    'achievement_progress', 'achievement_stages', 'achievements_launched',
                    'conversations', 'messages', 'conversation_events', 'calls',
                )}


async def test_gift_body_scope_rejected_without_writes(make_client, settings):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE)
    await draw_them(client, clock, 1)
    clock.advance(days=2)  # even the pending daily grant must remain untouched
    before = gameplay_snapshot(settings.database_path)
    for work in ['demo', None, '', 'SC', ' sc ', 1, False, [], {}]:
        for attempt in range(2):
            response = await client.post('/api/characters/mano/gifts',
                                         json={'gift_id': 'cake', 'work_id': work})
            assert gameplay_snapshot(settings.database_path) == before, (work, attempt, response.text)
            assert response.status_code == 409, (work, attempt, response.text)


@pytest.mark.parametrize('scope', [{}, {'work_id': 'sc'}])
async def test_two_legal_sc_gifts_keep_existing_semantics(make_client, settings, scope):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, CAKE)
    await draw_them(client, clock, 2)
    for left in (1, 0):
        response = await client.post('/api/characters/mano/gifts',
                                     json={'gift_id': 'cake', 'ignored_old_field': True, **scope})
        assert response.status_code == 200, (scope, left, response.text)
        assert response.json()['left'] == left
    assert (await backpack(client)).get('cake', 0) == 0


ROUTES = [
    ('GET', '/api/gifts', None),
    ('GET', '/api/gifts/catalog', None),
    ('POST', '/api/gifts/draws', {'all': False}),
    ('POST', '/api/characters/mano/gifts', {'gift_id': 'cake'}),
    ('POST', '/api/gifts/resets', None),
    ('GET', '/api/characters/mano/affinity', None),
    ('GET', '/api/characters/mano/achievements', None),
    ('GET', '/api/stats', None),
    ('GET', '/api/admin/gifts', None),
    ('POST', '/api/admin/gifts/reset-card-grants', None),
]
DECLARATIONS = [
    ({'X-Work-ID': 'demo'}, []),
    ({'x-work-id': 'SC'}, []),
    ({'X-Work-ID': ''}, []),
    ({'X-Work-ID': ' sc '}, []),
    ({}, [('work_id', 'demo')]),
    ({}, [('work_id', 'unknown')]),
    ({}, [('work_id', '')]),
    ({}, [('work_id', 'sc'), ('work_id', 'demo')]),
    ({}, [('work_id', 'demo'), ('work_id', 'sc')]),
    ({'X-Work-ID': 'sc'}, [('work_id', 'demo')]),
    ([('X-Work-ID', 'demo'), ('X-Work-ID', 'sc')], []),
]


@pytest.mark.parametrize('method,path,body', ROUTES)
async def test_all_gameplay_routes_reject_scope_before_loading_state(
    make_client, settings, github, method, path, body,
):
    from tests.test_admin_users import admin_client
    from tests.test_affinity import keep
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, CAKE)
    await draw_them(client, clock, 2)
    assert (await client.post('/api/characters/mano/gifts', json={'gift_id': 'cake'})).status_code == 200
    admin = await admin_client(make_client, github, clock=clock)
    assert (await admin.post('/api/admin/gifts/reset-card-grants')).status_code == 200
    keep(settings.database_path, 'producer', 0, clock.now, cooling=True)
    clock.advance(days=1)
    before = gameplay_snapshot(settings.database_path)
    requester = admin if '/admin/' in path else client
    for headers, params in DECLARATIONS:
        for attempt in range(2):
            response = await requester.request(method, path, json=body, headers=headers, params=params)
            scenario = (method, path, headers, params, attempt)
            assert gameplay_snapshot(settings.database_path) == before, scenario
            assert response.status_code == 409, (scenario, response.text)


@pytest.mark.parametrize('path,body', [
    ('/api/gifts/draws', {'all': False}),
    ('/api/characters/mano/gifts', {'gift_id': 'cake'}),
])
async def test_json_scope_cannot_be_overridden_by_sc_header_or_query(
    make_client, settings, path, body,
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE)
    await draw_them(client, clock, 1)
    clock.advance(days=1)
    before = gameplay_snapshot(settings.database_path)
    for work in ['demo', None, '', 'SC', ' sc ', 1, False, [], {}]:
        response = await client.post(path, json={**body, 'work_id': work},
                                     headers={'X-Work-ID': 'sc'}, params={'work_id': 'sc'})
        assert gameplay_snapshot(settings.database_path) == before, (path, work)
        assert response.status_code == 409, (path, work, response.text)


async def test_invalid_read_never_initializes_balance_and_explicit_sc_draw_works(make_client, settings):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE)
    before = gameplay_snapshot(settings.database_path)
    assert before['gift_balances'] == []
    for attempt in range(2):
        response = await client.get('/api/gifts', params={'work_id': 'demo'})
        assert response.status_code == 409, ('new-user', attempt, response.text)
        assert gameplay_snapshot(settings.database_path) == before, ('new-user', attempt)
    response = await client.post('/api/gifts/draws', json={'all': False, 'work_id': 'sc'},
                                 headers={'X-Work-ID': 'sc'}, params={'work_id': 'sc'})
    assert response.status_code == 200, response.text
    assert response.json()['drawn'] == [{'kind': 'gift', 'gift_id': 'cake'}]
    assert response.json()['draws'] == 0


async def test_real_gateway_preserves_scope_and_rejects_without_writes(make_client, settings, github):
    import asyncio
    import os
    from pathlib import Path
    import socket

    import httpx
    import uvicorn

    from tests.test_admin_users import admin_client
    from tests.test_affinity import keep

    binary = os.environ.get('EIDOLON_CORE_BINARY')
    if not binary:
        pytest.skip('set EIDOLON_CORE_BINARY for real Go/Python gameplay acceptance')
    clock = FakeClock()
    original = holding(make_client, clock, CAKE, CAKE, CAKE)
    await draw_them(original, clock, 2)
    admin = await admin_client(make_client, github, clock=clock)
    assert (await admin.post('/api/admin/gifts/reset-card-grants')).status_code == 200
    keep(settings.database_path, 'producer', 0, clock.now, cooling=True)
    clock.advance(days=1)
    assert isinstance(original._transport, httpx.ASGITransport)
    server = uvicorn.Server(uvicorn.Config(original._transport.app, lifespan='off', log_level='error'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if server_task.done():
                    await server_task
                    raise AssertionError('test upstream stopped')
                await asyncio.sleep(0.01)
        root = Path(__file__).resolve().parents[2]
        process = await asyncio.create_subprocess_exec(
            binary, '-listen', '127.0.0.1:0', '-legacy', f'http://127.0.0.1:{listener.getsockname()[1]}',
            '-packs', f'{root}/core/packs,{root}/core/testdata/packs', stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        ready = await asyncio.wait_for(process.stderr.readline(), 10)
        assert b'catalog listening on ' in ready, ready
        address = ready.decode().strip().split('catalog listening on ', 1)[1]
        async with httpx.AsyncClient(base_url=f'http://{address}', headers={'Origin': 'http://localhost'}, trust_env=False) as gateway:
            for method, path, body in ROUTES:
                user = admin if '/admin/' in path else original
                gateway.cookies = {cookie.name: cookie.value for cookie in user.cookies.jar
                                   if cookie.value is not None}
                for headers, params in DECLARATIONS:
                    before = gameplay_snapshot(settings.database_path)
                    if headers == {'X-Work-ID': ' sc '}:
                        # HTTPX refuses surrounding header whitespace before sending.
                        # ASGI tests above exercise an unnormalised value; this is
                        # transport rejection, not evidence of server rejection.
                        with pytest.raises(httpx.LocalProtocolError):
                            await gateway.request(method, path, json=body, headers=headers, params=params)
                        assert gameplay_snapshot(settings.database_path) == before, ('gateway-client', path)
                        continue
                    response = await gateway.request(method, path, json=body, headers=headers, params=params)
                    scenario = ('gateway', method, path, headers, params)
                    assert gameplay_snapshot(settings.database_path) == before, scenario
                    assert response.status_code == 409, (scenario, response.text)
            gateway.cookies = {cookie.name: cookie.value for cookie in original.cookies.jar
                               if cookie.value is not None}
            for _, path, body in ROUTES[2:4]:
                for work in ('demo', None, '', 'SC', ' sc ', 1, False, [], {}):
                    before = gameplay_snapshot(settings.database_path)
                    response = await gateway.post(path, json={**body, 'work_id': work},
                                                  headers={'X-Work-ID': 'sc'}, params={'work_id': 'sc'})
                    scenario = ('gateway', path, work)
                    assert gameplay_snapshot(settings.database_path) == before, scenario
                    assert response.status_code == 409, (scenario, response.text)
            for body in ({'gift_id': 'cake'}, {'gift_id': 'cake', 'work_id': 'sc'}):
                response = await gateway.post('/api/characters/mano/gifts', json=body,
                                              headers={'X-Work-ID': 'sc'}, params=[('work_id', 'sc'), ('work_id', 'sc')])
                assert response.status_code == 200, ('gateway', body, response.text)
            assert (await backpack(original)).get('cake', 0) == 0
            before_draws = (await gateway.get('/api/gifts')).json()['draws']
            response = await gateway.post('/api/gifts/draws', json={'all': False, 'work_id': 'sc'})
            assert response.status_code == 200, ('gateway', 'SC draw', response.text)
            assert response.json()['draws'] == before_draws - 1
            assert response.json()['drawn'] == [{'kind': 'gift', 'gift_id': 'cake'}]
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
            await asyncio.wait_for(server_task, 5)
        finally:
            listener.close()
