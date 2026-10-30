"""Complete public gifts panel, independently checked against the original Python GET."""
import os
import sqlite3
from dataclasses import replace

import pytest

from eidolon import achievements, happenings
from tests.fakes import FakeClock, FakeLLM
from tests.test_affinity_snapshot import settings as read_settings  # noqa: F401
from tests.test_delete_account import user_id
from tests.test_go_stats import gateway_for
from tests.test_sc_play_scope import gameplay_snapshot


@pytest.fixture
def settings(read_settings, tmp_path):
    token = tmp_path / 'panel-token'
    token.write_text('3' * 64)
    token.chmod(0o600)
    return replace(read_settings, core_gifts_panel_token_path=token)


async def test_go_gifts_panel_public_worked_example(make_client, settings, monkeypatch):
    clock = FakeClock()
    original = make_client(FakeLLM(), clock=clock)
    uid = user_id(settings.database_path, 'producer')
    monkeypatch.setattr(achievements, 'ALL_CHARACTERS', (
        achievements.Achievement('characters', (achievements.Stage(3, 1),)),
        achievements.Achievement('intimate_characters', (achievements.Stage(1, 2),)),
        achievements.Achievement('days', (achievements.Stage(7, 3),)),
        achievements.Achievement('draws', (achievements.Stage(10, 4),)),
    ))
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO daily_task_progress VALUES (?,?,?,?)', (uid, '2026-09-25', 17, 0))
        db.executemany('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', [
            (uid, 'turns', 'retired', 2), (uid, 'turns', 'mano', 1),
            (uid, 'tier', 'retired', 2), (uid, 'days', '', 4.9), (uid, 'draws', '', 11),
        ])
        db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', (uid, 'draws', '', 1, '20261029'))
    expected = {
        'open': True, 'draws': 1, 'max_draws': 7, 'backpack': [], 'reset_cards': 0,
        'next_draw_at': '2026-09-25T16:00:00+00:00', 'resettable': [],
        'characters': [
            {'id': 'mano', 'cooling': False, 'tier': 'trust', 'cooldown_until': None},
            {'id': 'kogane', 'cooling': False, 'tier': 'trust', 'cooldown_until': None},
        ], 'rewarded_at': None,
        'daily_tasks': {'tasks': [
            {'id': 'one_turn', 'done': True, 'progress': 1, 'goal': 1, 'draws': 1},
            {'id': 'one_gift', 'done': False, 'progress': 0, 'goal': 1, 'draws': 1},
            {'id': 'ten_turns', 'done': True, 'progress': 10, 'goal': 10, 'draws': 1},
        ], 'renews_at': '2026-09-25T16:00:00+00:00'},
        'achievements': [
            {'id': 'characters', 'progress': 2, 'stages': [{'goal': 3, 'draws': 1, 'unlocked_at': None}]},
            {'id': 'intimate_characters', 'progress': 1, 'stages': [{'goal': 1, 'draws': 2, 'unlocked_at': None}]},
            {'id': 'days', 'progress': 4, 'stages': [{'goal': 7, 'draws': 3, 'unlocked_at': None}]},
            {'id': 'draws', 'progress': 11, 'stages': [{'goal': 10, 'draws': 4, 'unlocked_at': '2026-10-29T00:00:00'}]},
        ],
    }
    assert (await original.get('/api/gifts')).json() == expected

    def unavailable(*args, **kwargs):
        raise RuntimeError('old Python panel projection deliberately unavailable')

    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag=os.environ.get('EIDOLON_GIFTS_PANEL_TEST_FLAG', '-gifts-panel-token-file'),
                           token_file=getattr(settings, 'core_gifts_panel_token_path', None)) as (gateway, _):
        with monkeypatch.context() as patch:
            patch.setattr(happenings.Happenings, 'daily_tasks', unavailable)
            patch.setattr(happenings.Happenings, 'of_all', unavailable)
            response = await gateway.get('/api/gifts')
        assert response.status_code == 200, ('complete public worked example', response.text)
        assert response.json() == expected


def restore_business(path, snapshot):
    """Restore only this test's synthetic tables; sessions retain their real middleware use."""
    with sqlite3.connect(path) as db:
        for table in reversed(snapshot):
            db.execute(f'DELETE FROM "{table}"')
        for table, rows in snapshot.items():
            if rows:
                placeholders = ','.join('?' for _ in rows[0])
                db.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)


async def test_go_gifts_panel_sql_oracle_and_actual_side_effects(make_client, settings, monkeypatch, subtests):
    """Each old/native request starts at the same SQL state, including no balance row."""
    clock = FakeClock()
    original = make_client(FakeLLM(), clock=clock)
    original._transport.raise_app_exceptions = False
    backend = original._transport.app.state.backend
    uid = user_id(settings.database_path, 'producer')
    blank = gameplay_snapshot(settings.database_path)
    default_rules, default_tasks = achievements.ALL_CHARACTERS, happenings.DAILY_TASKS
    turns = (happenings.DailyTask('turns', 'turns', 9007199254740993),)
    one = (achievements.Achievement('draws', (achievements.Stage(10, 1),)),)
    repeated = (achievements.Achievement('days', (achievements.Stage(1, 1),)),
                achievements.Achievement('days', (achievements.Stage(100, -2),)))
    cases = [
        ('new user', {}),
        ('daily early TEXT', {'daily': ('bad', 0)}),
        ('daily early BLOB', {'daily': (b'12', 0)}),
        ('global early TEXT', {'counts': [('turns', 'retired', 'bad')]}),
        ('global unknown bad date', {'stages': [('unknown', 99, 'bad')]}),
        ('global unknown BLOB date', {'stages': [('unknown', 99, b'20261029')]}),
        ('global late TEXT', {'counts': [('draws', '', 'bad')]}),
        ('global late BLOB', {'counts': [('draws', '', b'bad')]}),
        ('global late inf', {'counts': [('draws', '', float('inf'))]}),
        ('daily late negative inf', {'daily': (float('-inf'), 0)}),
        ('candidate positive inf accepted', {'counts': [('turns', 'retired', float('inf')), ('tier', b'', float('inf'))]}),
        ('candidate exact mixed floats', {'counts': [('turns', 'a', .9999999999999999), ('turns', 'b', 1.0), ('tier', 'c', 1.9999999999999998)]}),
        ('BLOB empty character is candidate', {'counts': [('turns', b'', 1), (b'turns', 'retired', 'bad'), ('turns', '', 'ignored'), ('old', 'x', b'bad')]}),
        ('i64 exact min comparison', {'daily': (9007199254740993, -3), 'tasks': turns, 'counts': [('draws', '', 9223372036854775807)]}),
        ('REAL exact min comparison', {'daily': (9007199254740992.0, 0), 'tasks': turns}),
        ('equal REAL min retains REAL', {'daily': (float(2 ** 63), 0),
                                        'tasks': (happenings.DailyTask('turns', 'turns', 2 ** 63),)}),
        ('negative fractional daily preserved', {'daily': (-1.9, 0), 'counts': [('draws', '', -1.9)]}),
        ('positive inf daily clamps', {'daily': (float('inf'), 0)}),
        ('unicode late int', {'counts': [('draws', '', '\u0085١_２\u3000')]}),
        ('blob late int', {'counts': [('draws', '', b' 1_2 ')]}),
        ('digit limit late', {'counts': [('draws', '', '0' * 4301)]}),
        ('all global stages including extra', {'rules': one, 'stages': [('draws', 1.0, '20261029'), ('old', 9, '2026-10-30T09:00:00+09:00'),
                                                                 ('draws', 1.5, '20261030'), ('draws', '1_0', '20261030'), ('draws', b'1', '20261030')]}),
        ('empty rules', {'rules': (), 'tasks': ()}),
        ('duplicate ids and changed stages', {'rules': repeated, 'tasks': (happenings.DailyTask('same', 'gifts', -1, 2), happenings.DailyTask('same', 'turns', 3, -1)),
                                             'counts': [('days', '', 4)], 'stages': [('days', 1, '20261029')]}),
        ('tier shown only late', {'rules': (achievements.Achievement('tier', (achievements.Stage(1, 1),)),), 'counts': [('tier', '', -1)]}),
        ('bad tier index late', {'rules': (achievements.Achievement('tier', (achievements.Stage(1, 1),)),), 'counts': [('tier', '', 3)]}),
        ('old daily ignored', {'day': '2026-09-24', 'daily': ('bad', b'bad')}),
        ('closed ignores bad history', {'closed': True, 'daily': ('bad', 0), 'stages': [('old', 1, 'bad')]}),
        ('daily due cap', {'balance': (6, '2026-09-01', 2)}),
        ('over cap preserved', {'balance': (19, '2026-09-01', 2)}),
        ('no debt', {'balance': (4, '2026-09-25', 2)}),
        ('reopened waterline', {'balance': (0, '2026-09-01', 0), 'opened': (1, '2026-09-25T01:00:00+00:00')}),
    ]
    original_state = backend.gifts.state
    calls = []

    async def traced_state(*args):
        calls.append(args)
        return await original_state(*args)

    monkeypatch.setattr(backend.gifts, 'state', traced_state)
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path) as (gateway, _):
        for label, case in cases:
            restore_business(settings.database_path, blank)
            monkeypatch.setattr(achievements, 'ALL_CHARACTERS', case.get('rules', default_rules))
            monkeypatch.setattr(happenings, 'DAILY_TASKS', case.get('tasks', default_tasks))
            monkeypatch.setattr(type(backend.config), 'affinity_on', property(lambda _, c=case: not c.get('closed', False)))
            with sqlite3.connect(settings.database_path) as db:
                if 'daily' in case:
                    db.execute('INSERT INTO daily_task_progress VALUES (?,?,?,?)', (uid, case.get('day', '2026-09-25'), *case['daily']))
                db.executemany('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', [(uid, a, c, n) for a, c, n in case.get('counts', ())])
                db.executemany('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', [(uid, a, '', n, at) for a, n, at in case.get('stages', ())])
                if 'balance' in case:
                    db.execute('INSERT INTO gift_balances VALUES (?,?,?,?)', (uid, *case['balance']))
                if 'opened' in case:
                    db.execute('UPDATE gifts_opened SET open=?,opened_at=?', case['opened'])
            before = gameplay_snapshot(settings.database_path)
            count = len(calls)
            expected = await original.get('/api/gifts')
            expected_calls = len(calls) - count
            after = gameplay_snapshot(settings.database_path)
            restore_business(settings.database_path, before)
            count = len(calls)
            actual = await gateway.get('/api/gifts')
            context = f'user=producer scope=sc phase=public case={label}'
            if actual.status_code != expected.status_code:
                print('SEMANTIC COUNTEREXAMPLE', context, 'original=', expected.text, 'native=', actual.text,
                      'original_after=', after, 'native_after=', gameplay_snapshot(settings.database_path))
            with subtests.test(case=label, phase='public'):
                assert actual.status_code == expected.status_code, (context, expected.text, actual.text)
            if actual.status_code != expected.status_code:
                continue
            if actual.status_code == 200:
                assert actual.json() == expected.json(), context
                if label == 'equal REAL min retains REAL':
                    assert isinstance(actual.json()['daily_tasks']['tasks'][0]['progress'], float), context
            else:
                assert actual.text == expected.text == 'Internal Server Error', context
                assert 'set-cookie' not in actual.headers, context
            assert len(calls) - count == expected_calls, context
            assert gameplay_snapshot(settings.database_path) == after, context
            # An independent literal pins the critical first-use boundary as well as the oracle.
            if label.startswith(('daily early', 'global early', 'global unknown')):
                assert expected_calls == 0 and not after['gift_balances'], context
            if label.startswith(('global late', 'daily late')):
                assert expected_calls == 1 and after['gift_balances'][0][1] == 1, context
            repeated_response = await gateway.get('/api/gifts')
            assert repeated_response.status_code == actual.status_code, context
            assert gameplay_snapshot(settings.database_path) == after, (context, 'repeat adds no rewards or daily draws')
        print(f'PASS {len(cases)} SQL oracle scenarios from identical business prestate; complete 14-table afterstates and state boundaries')


async def test_go_gifts_panel_base_serialization_oracle(make_client, settings, monkeypatch, subtests):
    """Pin the unchanged typed-dict FastAPI encoding before choosing its wire adapter."""
    clock = FakeClock()
    original = make_client(FakeLLM(), clock=clock)
    original._transport.raise_app_exceptions = False
    uid = user_id(settings.database_path, 'producer')
    backend = original._transport.app.state.backend
    blank = gameplay_snapshot(settings.database_path)
    state = backend.gifts.state
    calls = []

    async def traced(*args):
        calls.append(args)
        return await state(*args)

    monkeypatch.setattr(backend.gifts, 'state', traced)
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path) as (gateway, _):
        for label, draws, cards, output in (
                ('base negative inf', float('-inf'), 0, {'draws': None, 'reset_cards': 0}),
                ('base positive inf', float('inf'), 0, {'draws': None, 'reset_cards': 0}),
                ('base UTF8 BLOB draws', b'12', 0, {'draws': '12', 'reset_cards': 0}),
                ('base UTF8 BLOB cards', 2, '１２'.encode(), {'draws': 2, 'reset_cards': '１２'}),
                ('base invalid UTF8 BLOB', b'\xff', 0, None)):
            restore_business(settings.database_path, blank)
            with sqlite3.connect(settings.database_path) as db:
                db.execute('INSERT INTO gift_balances VALUES (?,?,?,?)', (uid, draws, '2026-09-25', cards))
                db.execute('UPDATE sessions SET last_active_at=?', (clock.now.timestamp() - 7200,))
            before = gameplay_snapshot(settings.database_path)
            calls.clear()
            expected = await original.get('/api/gifts')
            after = gameplay_snapshot(settings.database_path)
            assert expected.status_code == (500 if output is None else 200) and len(calls) == 1, (label, expected.text)
            if output is not None:
                assert {k: expected.json()[k] for k in output} == output, label
            assert len(expected.headers.get_list('set-cookie')) == (0 if output is None else 1), label
            restore_business(settings.database_path, before)
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE sessions SET last_active_at=?', (clock.now.timestamp() - 7200,))
            calls.clear()
            actual = await gateway.get('/api/gifts')
            actual_after = gameplay_snapshot(settings.database_path)
            print('BASE SERIALIZATION', label, 'original=', expected.text, 'original_cookies=', expected.headers.get_list('set-cookie'),
                  'native=', actual.text, 'native_cookies=', actual.headers.get_list('set-cookie'),
                  'state_calls=', len(calls), 'original_after=', after, 'native_after=', actual_after)
            assert len(calls) == 1 and actual_after == after, label
            with subtests.test(case=label, phase='base serialization'):
                assert actual.status_code == expected.status_code, (label, actual.text)
                if output is not None:
                    assert actual.json() == expected.json(), label
                else:
                    assert actual.text == expected.text == 'Internal Server Error', label
                assert len(actual.headers.get_list('set-cookie')) == (0 if output is None else 1), label


async def test_go_gifts_panel_phase_clock_and_open_samples(make_client, settings, monkeypatch):
    from datetime import datetime

    start = datetime.fromisoformat('2026-09-25T15:59:59+00:00')
    clock = FakeClock(start)
    original = make_client(FakeLLM(), clock=clock)
    backend = original._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    opened, selected = [True], ['daily', 'midnight']
    read, notice = backend.database.read, backend.gifts.notice

    def change():
        if selected[1] == 'midnight':
            clock.advance(seconds=2)
        else:
            opened[0] = False

    async def phased_read(body, *args):
        found = await read(body, *args)
        name = getattr(body, '__name__', '')
        if selected[0] == 'daily' and name == '_counts' or selected[0] == 'all' and name in ('_all_raw', 'of_all'):
            change()
        return found

    async def phased_notice():
        await notice()
        if selected[0] == 'notice':
            change()

    monkeypatch.setattr(backend.database, 'read', phased_read)
    monkeypatch.setattr(backend.gifts, 'notice', phased_notice)
    monkeypatch.setattr(backend.gifts, '_is_open', lambda: opened[0])
    monkeypatch.setattr(backend.happenings, '_is_open', lambda: opened[0])
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path) as (gateway, _):
        for phase in ('daily', 'all', 'notice'):
            for action in ('midnight', 'close'):
                selected[:] = [phase, action]
                restore_business(settings.database_path, before)
                clock.now, opened[0] = start, True
                expected = await original.get('/api/gifts')
                after = gameplay_snapshot(settings.database_path)
                restore_business(settings.database_path, before)
                clock.now, opened[0] = start, True
                got = await gateway.get('/api/gifts')
                context = f'phase={phase} action={action} scope=sc user=producer'
                assert got.status_code == expected.status_code == 200, (context, got.text)
                assert got.json() == expected.json(), context
                assert gameplay_snapshot(settings.database_path) == after, context
                if action == 'midnight':
                    assert got.json()['daily_tasks']['renews_at'] == '2026-09-25T16:00:00+00:00', context
                    assert got.json()['next_draw_at'] == '2026-09-26T16:00:00+00:00', context
                else:
                    assert got.json()['open'] is False and not after['gift_balances'], context
        print('PASS six phase-specific midnight/open transitions against original GET and all business tables')


async def test_go_gifts_panel_inventory_affinity_and_concurrent_actions(make_client, settings, characters, monkeypatch):
    import asyncio
    from datetime import timedelta

    from eidolon.gift_catalog import GIFTS_FILE
    from tests.test_affinity import keep
    from tests.test_gift_catalog import gift
    from tests.fakes import FakeRandom

    characters.write(GIFTS_FILE, gift('cake') + gift('retired', retired=True) + gift('tea')
                     + gift('cocoa', 'uncommon') + gift('plush', 'rare'))
    clock = FakeClock()
    original = make_client(FakeLLM(), clock=clock, random=FakeRandom(1, 0))
    other = make_client(FakeLLM(), 'other', clock=clock)
    uid = user_id(settings.database_path, 'producer')
    with sqlite3.connect(settings.database_path) as db:
        db.executemany('INSERT INTO gift_backpacks VALUES (?,?,?)', [(uid, 'tea', 2), (uid, 'retired', 1), (uid, 'cake', 2), (uid, 'removed', 9)])
    keep(settings.database_path, 'producer', 0, clock.now - timedelta(hours=12), cooling=True)
    before = gameplay_snapshot(settings.database_path)
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path) as (gateway, _):
        expected = await original.get('/api/gifts')
        after = gameplay_snapshot(settings.database_path)
        restore_business(settings.database_path, before)
        concurrent = await asyncio.gather(*(gateway.get('/api/gifts') for _ in range(5)))
        assert all(r.status_code == 200 and r.json() == expected.json() for r in concurrent)
        assert gameplay_snapshot(settings.database_path) == after
        assert expected.json()['backpack'] == [{'gift_id': 'cake', 'count': 2}, {'gift_id': 'retired', 'count': 1}, {'gift_id': 'tea', 'count': 2}]
        gateway.cookies = {c.name: c.value for c in other.cookies.jar}
        isolated = await gateway.get('/api/gifts', params={'user_id': uid})
        assert isolated.status_code == 200 and isolated.json()['backpack'] == []
        gateway.cookies = {c.name: c.value for c in original.cookies.jar}
        # Drawing and giving still use the old writer transactions; overlapping reads
        # may observe either side, but cannot duplicate today's grant or consume twice.
        responses = await asyncio.gather(gateway.get('/api/gifts'),
                                        gateway.post('/api/gifts/draws', json={}, headers={'Origin': 'http://localhost'}),
                                        gateway.post('/api/characters/mano/gifts', json={'gift_id': 'cake'}, headers={'Origin': 'http://localhost'}))
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        final = (await gateway.get('/api/gifts')).json()
        with sqlite3.connect(settings.database_path) as db:
            balance = db.execute('SELECT draws FROM gift_balances WHERE user_id=?', (uid,)).fetchone()[0]
            rewards = db.execute('SELECT key FROM gift_rewards WHERE user_id=? ORDER BY key', (uid,)).fetchall()
            counts = dict(db.execute('SELECT gift_id,count FROM gift_backpacks WHERE user_id=?', (uid,)))
        assert final['draws'] == balance
        assert len(rewards) == len(set(rewards)) and ('daily_task:2026-09-25:one_gift',) in rewards
        assert counts['cake'] == 2  # one drawn, one given, without losing either update


async def test_go_gifts_panel_legacy_termination_is_stable(make_client, settings, monkeypatch, tmp_path):
    import asyncio

    from uvicorn.protocols.http import auto
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

    original = make_client(FakeLLM(), clock=FakeClock())
    uid = user_id(settings.database_path, 'producer')
    blank = gameplay_snapshot(settings.database_path)
    frames = []
    accepted, request_connections, closed = [], [], set()
    all_closed = asyncio.Event()

    class ObservedConnections(HttpToolsProtocol):
        def connection_made(self, transport):
            connection_id = len(accepted) + 1
            accepted.append((connection_id, transport))
            self.test_connection_id = connection_id
            app = self.app

            async def bound(scope, receive, send) -> None:
                await app(scope | {'test.connection_id': connection_id}, receive, send)

            self.app = bound
            super().connection_made(transport)

        def connection_lost(self, exc):
            super().connection_lost(exc)
            closed.add(self.test_connection_id)
            if len(closed) == 300:
                all_closed.set()

    # Observe actual accepted TCP lifetimes. Local ports can be reused across
    # distinct connections, so a (host, port) tuple is not a connection identity.
    monkeypatch.setattr(auto, 'AutoHTTPProtocol', ObservedConnections)

    async def observed(scope, receive, send):
        if scope.get('path') == '/api/_core/sc-gifts-panel':
            request_connections.append(scope['test.connection_id'])
        async def receiving():
            message = await receive()
            frames.append(('receive', message))
            return message

        async def sending(message):
            frames.append(('send', message))
            try:
                await send(message)
            except BaseException as exc:
                frames.append(('send-failed', repr(exc)))
                raise
            frames.append(('send-complete', message['type'], message.get('more_body')))

        await original._transport.app(scope, receiving, sending)

    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path,
                           app_override=observed) as (gateway, _):
        for scenario in ('daily compare', 'global compare', 'global date', 'global int', 'base UTF8'):
            for attempt in range(60):
                restore_business(settings.database_path, blank)
                with sqlite3.connect(settings.database_path) as db:
                    if scenario == 'daily compare':
                        db.execute('INSERT INTO daily_task_progress VALUES (?,?,?,0)', (uid, '2026-09-25', 'bad'))
                    elif scenario in ('global compare', 'global int'):
                        a, c = ('turns', 'retired') if scenario == 'global compare' else ('draws', '')
                        db.execute('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', (uid, a, c, 'bad'))
                    elif scenario == 'global date':
                        db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', (uid, 'old', '', 1, 'bad'))
                    else:
                        db.execute('INSERT INTO gift_balances VALUES (?,?,?,0)', (uid, b'\xff', '2026-09-25'))
                frames.clear()
                response = await gateway.get('/api/gifts')
                if response.status_code != 500:
                    (tmp_path / 'termination-failure.txt').write_text(repr((scenario, attempt, frames)))
                    print('TERMINATION TRACE', scenario, attempt, repr(frames), flush=True)
                assert response.status_code == 500 and response.text == 'Internal Server Error', (scenario, attempt, response.status_code, response.text, frames)
                assert 'set-cookie' not in response.headers, (scenario, attempt)
        await asyncio.wait_for(all_closed.wait(), 2)
        assert len(accepted) == len(request_connections) == len(set(request_connections)) == len(closed) == 300, (
            'phase=terminal scope=sc user=producer: one accepted TCP lifetime per public GET, no replay or reuse',
            len(accepted), len(request_connections), len(set(request_connections)), len(closed))
        print('PASS 300 repeated real TCP legacy terminations across Go abort, Python raw error, Go finish-legacy and Python base error')


async def test_go_gifts_panel_loss_and_cookie_policy(make_client, settings, monkeypatch):
    original = make_client(FakeLLM(), clock=FakeClock())
    backend = original._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    uid = user_id(settings.database_path, 'producer')
    use, state = backend.store.use_session, backend.gifts.state
    uses, states = [], []

    async def traced_use(*args):
        uses.append(args)
        return await use(*args)

    async def traced_state(*args):
        states.append(args)
        return await state(*args)

    monkeypatch.setattr(backend.store, 'use_session', traced_use)
    monkeypatch.setattr(backend.gifts, 'state', traced_state)

    class Fault:
        def __init__(self):
            self.stage, self.requests, self.hit = 'normal', 0, False

        async def __call__(self, scope, receive, send):
            if scope.get('path') == '/api/_core/sc-gifts-panel':
                self.requests += 1

            async def sending(message: dict):
                if message['type'] == 'http.response.start':
                    message = dict(message, headers=[*message['headers'], (b'set-cookie', b'extra=1; HttpOnly')])
                body = message.get('body', b'')
                target = {'daily': b'["daily",', 'all': b'["all",', 'state': b'["state",', 'end': b'["end"]'}.get(self.stage)
                if target and body.startswith(target):
                    self.hit = True
                    raise RuntimeError('test-only actual private response lost at ' + self.stage)
                await send(message)

            await original._transport.app(scope, receive, sending)

    fault = Fault()
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path,
                           app_override=fault) as (gateway, _):
        for stage in ('normal', 'daily', 'all', 'state', 'end', 'early legacy', 'late legacy'):
            restore_business(settings.database_path, before)
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE sessions SET last_active_at=?', (backend.clock().timestamp() - 7200,))
                if stage == 'early legacy':
                    db.execute('INSERT INTO daily_task_progress VALUES (?,?,?,0)', (uid, '2026-09-25', 'bad'))
                if stage == 'late legacy':
                    db.execute('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', (uid, 'draws', '', 'bad'))
            uses.clear()
            states.clear()
            fault.stage, fault.hit, fault.requests = stage, False, 0
            response = await gateway.get('/api/gifts', headers={'Origin': 'https://foreign.example', 'Authorization': 'Bearer untrusted'})
            assert len(uses) == fault.requests == 1, stage
            cookies = response.headers.get_list('set-cookie')
            after = gameplay_snapshot(settings.database_path)
            if stage == 'normal':
                assert response.status_code == 200 and len(cookies) == 2 and cookies[-1] == 'extra=1; HttpOnly', ('phase=public', stage, response.status_code, cookies)
            elif stage.endswith('legacy'):
                assert response.status_code == 500 and not cookies, (stage, response.text, cookies)
            else:
                assert response.status_code == 502 and len(cookies) == 2 and fault.hit, (stage, response.text, cookies)
            expected_states = 0 if stage in ('daily', 'all', 'early legacy') else 1
            assert len(states) == expected_states, stage
            assert len(after['gift_balances']) == expected_states, stage
            assert not after['gift_rewards'], stage
        for params, headers in (({'work_id': 'demo'}, {}), ((('work_id', 'sc'), ('work_id', 'demo')), {}),
                                ({}, [('X-Work-ID', 'sc'), ('X-Work-ID', 'demo')])):
            assert (await gateway.get('/api/gifts', params=params, headers=headers)).status_code == 409, ('phase=scope', params, headers)
        for path in ('/api/_core/sc-gifts-panel', '/api/%5fcore/sc-gifts-panel'):
            assert (await gateway.get(path, headers={'Authorization': 'Bearer ' + '3' * 64})).status_code == 404, ('phase=public-private-rejection', path)
        gateway.cookies.clear()
        assert (await gateway.get('/api/gifts')).status_code == 401


async def test_go_gifts_panel_pre_stream_identity_errors_remain_stable(make_client, settings):
    original = make_client(FakeLLM(), clock=FakeClock())
    uid = user_id(settings.database_path, 'producer')
    before = gameplay_snapshot(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        consented = db.execute('SELECT consented_at FROM users WHERE id=?', (uid,)).fetchone()[0]
    original_cookies = {c.name: c.value for c in original.cookies.jar}
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path) as (gateway, _):
        for status in (401, 403, 428):
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE users SET banned_at=?,consented_at=? WHERE id=?',
                           ('2026-09-25T12:00:00+00:00' if status == 403 else None,
                            None if status == 428 else consented, uid))
            cookies = {} if status == 401 else original_cookies
            gateway.cookies = cookies
            original.cookies = cookies
            expected = await original.get('/api/gifts')
            original.cookies = original_cookies
            assert expected.status_code == status, ('phase=identity direct', status, expected.text)
            for attempt in range(60):
                response = await gateway.get('/api/gifts')
                assert response.status_code == status and response.json() == expected.json(), (
                    'phase=identity before private200 scope=sc user=producer', status, attempt, response.status_code, response.text)
            assert gameplay_snapshot(settings.database_path) == before, ('phase=identity no business writes', status)
        print('PASS 180 real pre-stream identity failures: 401/403/428 retain original public status and body')


async def test_go_gifts_panel_deployment_modes(make_client, settings):
    import importlib.util
    from pathlib import Path
    import sys

    original = make_client(FakeLLM())
    backend = original._transport.app.state.backend
    before = gameplay_snapshot(settings.database_path)
    expected = (await original.get('/api/gifts')).json()
    for binary_env in ('EIDOLON_GIFTS_PANEL_OLD_BINARY', 'EIDOLON_GIFTS_PANEL_BINARY'):
        restore_business(settings.database_path, before)
        async with gateway_for(original, settings, binary_env=binary_env, token_flag='-affinity-token-file') as (gateway, _):
            response = await gateway.get('/api/gifts')
            assert response.status_code == 200 and response.json() == expected, binary_env
    path = Path(os.environ['EIDOLON_GIFTS_PANEL_BASELINE_APP'])
    spec = importlib.util.spec_from_file_location('gifts_panel_baseline_app', path)
    assert spec is not None and spec.loader is not None
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    old_app = baseline.create_app(backend)
    restore_business(settings.database_path, before)
    async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                           token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path,
                           app_override=old_app) as (gateway, _):
        response = await gateway.get('/api/gifts')
        assert response.status_code == 502 and 'unavailable' in response.text
        assert gameplay_snapshot(settings.database_path) == before
    print('PASS old Go/new Python, new Go flag off/new Python, new Go native/real old HEAD Python without panel route')


def test_gifts_panel_credentials_reject_aliases_and_unsafe_files(make_client, settings, tmp_path):
    from pathlib import Path
    import subprocess

    from eidolon.gifts_panel import add_gifts_panel
    from fastapi import FastAPI
    from tests.test_character_snapshot import CONFIG, export

    original = make_client(FakeLLM())
    backend = original._transport.app.state.backend
    read_alias = tmp_path / 'same-read-content'
    read_alias.write_bytes(settings.core_service_token_path.read_bytes())
    read_alias.chmod(0o600)
    create_alias = tmp_path / 'same-create-content'
    create_alias.write_text('2' * 64)
    create_alias.chmod(0o600)
    previous = backend.settings
    for token_path, create_path in ((read_alias, None), (create_alias, create_alias)):
        backend.settings = replace(previous, core_gifts_panel_token_path=token_path, core_sc_create_token_path=create_path)
        with pytest.raises(RuntimeError, match='distinct'):
            add_gifts_panel(FastAPI(), backend)
    backend.settings = previous
    unsafe = tmp_path / 'unsafe-panel-token'
    unsafe.write_text('3' * 64)
    unsafe.chmod(0o644)
    backend.settings = replace(previous, core_gifts_panel_token_path=unsafe)
    with pytest.raises(RuntimeError, match='invalid affinity service credential'):
        add_gifts_panel(FastAPI(), backend)
    backend.settings = previous
    binary = os.environ['EIDOLON_GIFTS_PANEL_BINARY']
    root = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / 'credential-check-content.json'
    exported = export(CONFIG, snapshot, with_gifts=True)
    assert exported.returncode == 0, exported.stderr.decode()
    for flag in ('-affinity-token-file', '-stats-token-file', '-timeline-token-file', '-summaries-token-file', '-achievements-token-file', '-sc-create-token-file'):
        # NewHandler startup compares contents even when the file paths differ.
        alias = tmp_path / ('same-panel-content-' + flag[1:])
        alias.write_bytes(settings.core_gifts_panel_token_path.read_bytes())
        alias.chmod(0o600)
        args = [binary, '-packs', f'{root}/core/packs,{root}/core/testdata/packs', '-gifts-panel-token-file', str(settings.core_gifts_panel_token_path), flag, str(alias)]
        if flag == '-sc-create-token-file':
            args.extend(('-content-snapshot', str(snapshot)))
        result = subprocess.run(args, capture_output=True, timeout=5)
        assert result.returncode != 0 and b'distinct' in result.stderr and b'catalog listening' not in result.stderr, (flag, result.stderr)


async def test_go_gifts_panel_process_loss_has_no_resumption_or_replay(make_client, settings, monkeypatch):
    import asyncio
    import httpx

    original = make_client(FakeLLM(), clock=FakeClock())
    before = gameplay_snapshot(settings.database_path)
    spawn = asyncio.create_subprocess_exec
    processes = []

    async def spawning(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        if args[0] == os.environ['EIDOLON_GIFTS_PANEL_BINARY']:
            processes.append(process)
        return process

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawning)

    class Held:
        def __init__(self, phase):
            self.phase = phase
            self.hit, self.release, self.disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()
            self.requests = 0

        async def __call__(self, scope, receive, send):
            self.requests += scope.get('path') == '/api/_core/sc-gifts-panel'

            async def receiving():
                message = await receive()
                if message['type'] == 'http.disconnect':
                    self.disconnected.set()
                return message

            async def sending(message: dict):
                if message.get('body', b'').startswith(b'["' + self.phase.encode() + b'",'):
                    self.hit.set()
                    await self.release.wait()
                await send(message)

            await original._transport.app(scope, receiving, sending)

    for phase, expected_balance in (('daily', 0), ('state', 1)):
        restore_business(settings.database_path, before)
        fault = Held(phase)
        async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                               token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path,
                               app_override=fault) as (gateway, _):
            request = asyncio.create_task(gateway.get('/api/gifts'))
            try:
                await asyncio.wait_for(fault.hit.wait(), 2)
                process = processes[-1]
                process.kill()
                await asyncio.wait_for(process.wait(), 2)
                with pytest.raises(httpx.HTTPError):
                    await asyncio.wait_for(request, 2)
                await asyncio.wait_for(fault.disconnected.wait(), 2)
                assert fault.requests == 1, (phase, 'no replay after process death')
                after = gameplay_snapshot(settings.database_path)
                assert len(after['gift_balances']) == expected_balance and not after['gift_rewards'], phase
            finally:
                fault.release.set()
                if not request.done():
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
        # Restarting the process creates a fresh dialog only for this explicit new GET.
        async with gateway_for(original, settings, binary_env='EIDOLON_GIFTS_PANEL_BINARY',
                               token_flag='-gifts-panel-token-file', token_file=settings.core_gifts_panel_token_path) as (restarted, _):
            response = await restarted.get('/api/gifts')
            assert response.status_code == 200 and response.json()['draws'] == 1, phase
            after = gameplay_snapshot(settings.database_path)
            assert len(after['gift_balances']) == 1 and after['gift_balances'][0][1] == 1 and not after['gift_rewards'], phase
