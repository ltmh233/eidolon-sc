"""Public SC character achievement projection against the independent Python oracle."""
import os
import sqlite3
from typing import cast

import httpx
from fastapi import FastAPI

from eidolon import achievements
from tests.fakes import FakeLLM
from tests.test_affinity_snapshot import settings as settings
from tests.test_delete_account import user_id
from tests.test_go_stats import gateway_for


async def test_go_achievements_public_worked_example(make_client, settings, monkeypatch):
    original = make_client(FakeLLM())
    uid = user_id(settings.database_path, 'producer')
    rules = (
        achievements.Achievement('turns', (achievements.Stage(10, 1),)),
        achievements.Achievement('call_time', (achievements.Stage(600, 1),)),
        achievements.Achievement('gifts', (achievements.Stage(1, 1),)),
        achievements.Achievement('tier', (achievements.Stage(1, 1),)),
    )
    monkeypatch.setattr(achievements, 'PER_CHARACTER', rules)
    with sqlite3.connect(settings.database_path) as db:
        db.executemany('INSERT INTO achievement_progress VALUES (?,?,?, ?,NULL)',
                       [(uid, name, 'mano', count) for name, count in
                        [('turns', 17), ('call_time', 1.9), ('gifts', 0), ('tier', -1), ('complete', 0)]])
        db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)',
                   (uid, 'turns', 'mano', 1, '20261029'))
    path = '/api/characters/mano/achievements'
    expected = {
        'achievements': [
            {'id': 'turns', 'progress': 17, 'stages': [{'goal': 10, 'draws': 1, 'unlocked_at': '2026-10-29T00:00:00'}]},
            {'id': 'call_time', 'progress': 1, 'stages': [{'goal': 600, 'draws': 1, 'unlocked_at': None}]},
            {'id': 'gifts', 'progress': 0, 'stages': [{'goal': 1, 'draws': 1, 'unlocked_at': None}]},
            {'id': 'tier', 'progress': 'close', 'stages': [{'goal': 'close', 'draws': 1, 'unlocked_at': None}]},
        ], 'complete': {'completed_at': None, 'reset_cards': 0},
    }
    assert (await original.get(path)).json() == expected
    assert isinstance(original._transport, httpx.ASGITransport)
    app = cast(FastAPI, original._transport.app)

    def unavailable(*args, **kwargs):
        raise RuntimeError('test-only old Python projection unavailable')

    # Initial red uses the old binary's supported affinity flag: achievements still proxy.
    # Native green uses the new achievements flag. The public request/expectation is identical.
    async with gateway_for(original, settings, binary_env='EIDOLON_ACHIEVEMENTS_BINARY',
                           token_flag=os.environ.get('EIDOLON_ACHIEVEMENTS_TEST_FLAG', '-achievements-token-file')) as (gateway, _):
        with monkeypatch.context() as patch:
            patch.setattr(achievements, 'hers', unavailable)
            patch.setattr(achievements.Hers, 'shown', unavailable)
            response = await gateway.get(path)
        assert response.status_code == 200, ('user=producer character=mano scope=sc contract=v1 worked example', response.text)
        assert response.json() == expected
        assert app.state.backend.config.affinity_on


async def test_go_achievements_sql_oracle_and_read_boundaries(make_client, settings, monkeypatch):
    import asyncio
    import sys
    import unicodedata
    from dataclasses import replace

    from tests.test_affinity_snapshot import TOKEN
    from tests.test_sc_play_scope import gameplay_snapshot

    original = make_client(FakeLLM())
    other = make_client(FakeLLM(), 'other')
    uid = user_id(settings.database_path, 'producer')
    path = '/api/characters/mano/achievements'
    assert isinstance(original._transport, httpx.ASGITransport)
    app = cast(FastAPI, original._transport.app)
    one = (achievements.Achievement('turns', (achievements.Stage(10, 1),)),)
    pair = (achievements.Achievement('turns', (achievements.Stage(10, 1), achievements.Stage(100, 2))),)
    cases = [
        ('defaults', None, (), ()),
        ('i64 precise', one, [('turns', 9007199254740993)], ()),
        ('i64 max', one, [('turns', 9223372036854775807)], ()),
        ('binary64 truncates', one, [('turns', -1.9)], ()),
        ('binary64 huge', one, [('turns', 1e100)], ()),
        ('binary64 maximum', one, [('turns', sys.float_info.max)], ()),
        ('binary64 inf legacy failure', one, [('turns', float('inf'))], ()),
        ('unicode digits and spaces', one, [('turns', '\u0085١_２\u3000')], ()),
        ('all Unicode14 decimal mappings', one, [('turns', ''.join(chr(n) for n in range(0x110000) if unicodedata.decimal(chr(n), None) is not None))], ()),
        ('all Unicode14 int whitespace', one, [('turns', ''.join(chr(n) for n in [*range(9, 14), 32, 0x85, 0xa0, 0x1680, *range(0x2000, 0x200b), 0x2028, 0x2029, 0x202f, 0x205f, 0x3000]) + '-１２')], ()),
        ('blob valid', one, [('turns', b'1_2')], ()),
        ('blob invalid', one, [('turns', b'\x8512')], ()),
        ('text invalid whitespace', one, [('turns', '\x1c12')], ()),
        ('text invalid underscore', one, [('turns', '1__2')], ()),
        ('text digit limit', one, [('turns', '0' * 4301)], ()),
        ('unused bad counts', one, [('old', b'bad'), ('complete', b'bad'), (b'turns', b'bad')], ()),
        ('all stages and stable same instant', pair, [('turns', 0)],
         [('turns', 2, '2026-10-29T00:00:00Z'), ('turns', 1, '2026-10-29T09:00:00+09:00'), ('old', 9, '20261030')]),
        ('aware offset seconds micros', one, (), [('turns', 1, '0001-01-01T00:00:00.000001+23:59:59.999999')]),
        ('naive max', pair, (), [('turns', 1, '2026-10-29'), ('turns', 2, '2026-10-30T01:00:00.123456789')]),
        ('missing stage mixed irrelevant time', pair, (), [('turns', 1, '2026-10-29'), ('old', 1, '2026-10-29Z'.replace('29Z', '29T00:00:00Z'))]),
        ('required mixed max fails before bad int', pair, [('turns', 'bad')], [('turns', 1, '2026-10-29'), ('turns', 2, '2026-10-29T00:00:00Z')]),
        ('extra stage is not int truncated', one, [('turns', 11)], [('turns', 1.5, '2026-10-29'), ('turns', '1_0', '2026-10-29'), ('turns', b'1', '2026-10-29')]),
        ('unknown invalid date still fails', one, (), [('old', 1.5, 'bad')]),
        ('blob date still fails', one, (), [('old', 1.5, b'2026-10-29')]),
        ('empty definitions', (), [('complete', 0)], [('old', 1, '20261029')]),
        ('empty stages', (achievements.Achievement('turns', ()),), [('turns', 12)], ()),
        ('rules reordered and added stage marker kept',
         (achievements.Achievement('gifts', (achievements.Stage(10000, 31),)), *pair),
         [('gifts', 5), ('complete', 0)], [('gifts', 1, '2026-10-29'), ('turns', 1, '2026-10-30')]),
    ]
    for value in (-1, -1.9, -2, 0, 2, 3):
        cases.append((f'tier rank {value}', (achievements.Achievement('tier', (achievements.Stage(1, 3),)),), [('tier', value)], ()))
    defaults = achievements.PER_CHARACTER
    async with gateway_for(original, settings, binary_env='EIDOLON_ACHIEVEMENTS_BINARY',
                           token_flag='-achievements-token-file') as (gateway, _):
        async def compare(label):
            # An independent oracle: original hers + shown against the actual SQL values.
            try:
                with sqlite3.connect(settings.database_path) as db:
                    expected = achievements.hers(db, uid, 'mano').shown()
            except (TypeError, ValueError, OverflowError, IndexError):
                expected = None
                status = 500
            else:
                status = 200
            got = await gateway.get(path)
            context = f'user=producer character=mano scope=sc contract=v1 case={label}'
            assert got.status_code == status, (context, got.status_code)
            if status == 200:
                assert got.json() == expected, context
            else:
                assert got.text == 'Internal Server Error', context
                assert got.headers['content-type'] == 'text/plain; charset=utf-8', context
            return got

        for label, rules, counts, stages in cases:
            monkeypatch.setattr(achievements, 'PER_CHARACTER', defaults if rules is None else rules)
            with sqlite3.connect(settings.database_path) as db:
                db.execute('DELETE FROM achievement_progress')
                db.execute('DELETE FROM achievement_stages')
                db.executemany('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', [(uid, k, 'mano', v) for k, v in counts])
                db.executemany('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', [(uid, k, 'mano', n, at) for k, n, at in stages])
            before = gameplay_snapshot(settings.database_path)
            await compare(label)
            assert gameplay_snapshot(settings.database_path) == before, label
        # D=0 is a real Python runtime profile, including more than 4300 leading zeroes.
        previous = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(0)
            with sqlite3.connect(settings.database_path) as db:
                db.execute('DELETE FROM achievement_progress')
                db.execute('DELETE FROM achievement_stages')
                db.execute('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', (uid, 'tier', 'mano', '0' * 5000 + '1_0'))
            monkeypatch.setattr(achievements, 'PER_CHARACTER', one)
            await compare('disabled digit limit unused text')
            with sqlite3.connect(settings.database_path) as db:
                db.execute("UPDATE achievement_progress SET achievement='turns'")
            await compare('disabled digit limit used text')
        finally:
            sys.set_int_max_str_digits(previous)
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE achievement_progress SET count=7")
        before_concurrent = gameplay_snapshot(settings.database_path)
        concurrent = await asyncio.gather(*(gateway.get(path) for _ in range(5)))
        assert all(response.status_code == 200 for response in concurrent), 'concurrent read public status'
        assert gameplay_snapshot(settings.database_path) == before_concurrent, 'concurrent reads have no business writes'
        for label, client in (('other user', other), ('owner user', original)):
            gateway.cookies = {c.name: c.value for c in client.cookies.jar if c.value is not None}
            expected = await client.get(path)
            assert (await gateway.get(path, params={'user_id': uid})).json() == expected.json(), (label, 'user cookie binds raw reader')
        for route in ('/api/_core/achievements/mano', '/api/%5fcore/achievements/mano'):
            denied = await gateway.get(route, headers={'Authorization': 'Bearer ' + TOKEN})
            assert denied.status_code == 404, route
        before = gameplay_snapshot(settings.database_path)
        for params, headers in (({'work_id': 'demo'}, {}), ({}, {'X-Work-ID': 'demo'}),
                                ((('work_id', 'sc'), ('work_id', 'demo')), {})):
            assert (await gateway.get(path, params=params, headers=headers)).status_code == 409, (params, headers)
        with monkeypatch.context() as patch:
            patch.setattr(type(app.state.backend.config), 'affinity_on', property(lambda _: False))
            # Closed mode must not parse even a corrupt historical date.
            with sqlite3.connect(settings.database_path) as db:
                db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', (uid, 'old', 'mano', 1, 'bad'))
            before = gameplay_snapshot(settings.database_path)
            assert (await gateway.get(path)).json() is None
            assert (await gateway.get('/api/characters/missing/achievements')).status_code == 404
        assert gameplay_snapshot(settings.database_path) == before
        gateway.cookies.clear()
        assert (await gateway.get(path)).status_code == 401
    # No configured service capability still refuses the new private endpoint.
    disabled = make_client(FakeLLM(), settings=replace(settings, core_service_token_path=None))
    assert (await disabled.get('/api/_core/achievements/mano', headers={'Authorization': 'Bearer ' + TOKEN})).status_code == 404
    print(f'PASS achievement independent SQL oracle {len(cases) + 2} scenarios; exact integer/profile/date stages, user/SC and no business writes')


async def test_go_achievements_deployment_modes_and_single_attempt(make_client, settings):
    import importlib.util
    from pathlib import Path
    import sys
    original = make_client(FakeLLM())
    path = '/api/characters/mano/achievements'
    expected = (await original.get(path)).json()
    # New Core disabled, and preserved old Core with new Python: original public route.
    for binary_env in ('EIDOLON_ACHIEVEMENTS_BINARY', 'EIDOLON_ACHIEVEMENTS_OLD_BINARY'):
        async with gateway_for(original, settings, binary_env=binary_env,
                               token_flag='-affinity-token-file') as (gateway, _):
            assert (await gateway.get(path)).json() == expected, binary_env
    baseline = os.environ.get('EIDOLON_ACHIEVEMENTS_BASELINE_APP')
    if not baseline:
        import pytest
        pytest.skip('set EIDOLON_ACHIEVEMENTS_BASELINE_APP to the fixed baseline app.py for real version acceptance')
    specification = importlib.util.spec_from_file_location('eidolon.achievement_baseline_app', Path(baseline))
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    assert isinstance(original._transport, httpx.ASGITransport)
    current = cast(FastAPI, original._transport.app)
    previous = module.create_app(current.state.backend)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=previous), base_url='http://localhost',
                                 cookies={c.name: c.value for c in original.cookies.jar if c.value is not None}) as direct_previous:
        assert (await direct_previous.get(path)).json() == expected, 'actual baseline public route still works'
        assert (await direct_previous.get('/api/_core/achievements/mano')).status_code == 404, 'actual baseline private route absent'
    calls = []

    async def observed_previous(scope, receive, send):
        if scope['type'] == 'http':
            calls.append(scope['path'])
        await previous(scope, receive, send)

    async with gateway_for(original, settings, binary_env='EIDOLON_ACHIEVEMENTS_BINARY',
                           token_flag='-achievements-token-file', app_override=observed_previous) as (gateway, _):
        response = await gateway.get(path)
        assert response.status_code == 502
        assert response.json() == {'detail': 'SC achievements unavailable'}
    assert calls == ['/api/_core/achievements/mano'], 'single attempt; no public fallback/version probe'


async def test_native_read_modes_do_not_join_reward_transactions(make_client, settings, monkeypatch):
    import asyncio
    from dataclasses import replace

    from eidolon import achievement_snapshot
    from tests.test_achievements import REPLY, state, turns
    from tests.test_sc_play_scope import gameplay_snapshot
    from tests.test_text_turn import send_turn, start_conversation

    monkeypatch.setattr(achievements, 'PER_CHARACTER',
                        (achievements.Achievement('turns', (achievements.Stage(10, 1),)),))
    for mode in ('native', 'private unavailable', 'disabled'):
        isolated = replace(settings, database_path=settings.database_path.with_name(mode.replace(' ', '-') + '.db'))
        client = make_client(FakeLLM(*[REPLY] * 11), settings=isolated)
        first = await start_conversation(client)
        second = await start_conversation(client)
        await turns(client, first, 9)
        before = await state(client)
        flag = '-affinity-token-file' if mode == 'disabled' else '-achievements-token-file'
        async with gateway_for(client, isolated, binary_env='EIDOLON_ACHIEVEMENTS_BINARY', token_flag=flag) as (gateway, _):
            gateway.headers['Origin'] = 'http://localhost:3000'
            with monkeypatch.context() as patch:
                if mode == 'private unavailable':
                    def unavailable(*args):
                        raise sqlite3.OperationalError('test-only private read unavailable')
                    patch.setattr(achievement_snapshot, '_read', unavailable)
                results = await asyncio.gather(send_turn(gateway, first, '一'), send_turn(gateway, second, '二'),
                                               gateway.get('/api/characters/mano/achievements'))
            assert [results[0][-1]['type'], results[1][-1]['type']] == ['done', 'done'], mode
            assert results[2].status_code == (500 if mode == 'private unavailable' else 200), mode
            after = await state(client)
            assert after['draws'] == before['draws'] + 2, (mode, 'one stage and one daily task')
            assert after['reset_cards'] == before['reset_cards'] + 1, (mode, 'one completion reward')
            direct = await client.get('/api/characters/mano/achievements')
            before_reads = gameplay_snapshot(isolated.database_path)
            native = await gateway.get('/api/characters/mano/achievements')
            assert native.json() == direct.json(), (mode, 'final public projection')
            assert native.json()['complete']['reset_cards'] == 0, (mode, 'historical completion marker')
            assert gameplay_snapshot(isolated.database_path) == before_reads, (mode, 'read did not reward again')
