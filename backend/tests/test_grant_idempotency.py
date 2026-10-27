"""Persistent admin grant outcomes through the authenticated HTTP boundary."""
import asyncio
import sqlite3
import pytest
from uuid import uuid4

from tests.test_reset_card_grants import settings as settings, cards, GRANT
from tests.test_admin_users import admin_client
from tests.fakes import FakeClock, FakeLLM


async def test_same_operation_replays_even_after_users_and_open_state_change(make_client, github, settings):
    clock = FakeClock()
    admin = await admin_client(make_client, github, clock=clock)
    make_client(FakeLLM(), clock=clock)
    key = str(uuid4())
    first = await admin.post(GRANT, headers={'Idempotency-Key': key})
    assert first.status_code == 200, first.text
    clock.advance(days=1)
    make_client(FakeLLM(), 'later', clock=clock)
    assert (await admin.put('/api/admin/config/affinity', json={'enabled': False})).status_code == 200
    again = await admin.post(GRANT, headers={'Idempotency-Key': key})
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert cards(settings.database_path, 'producer') == 1
    assert cards(settings.database_path, 'later') is None
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT count(*) FROM reset_card_grants').fetchone() == (1,)
    from tests.test_sc_play_scope import gameplay_snapshot
    new_key = str(uuid4())
    before = gameplay_snapshot(settings.database_path)
    refused = await admin.post(GRANT, headers={'Idempotency-Key': new_key})
    assert refused.status_code == 409, refused.text
    assert gameplay_snapshot(settings.database_path) == before
    assert (await admin.put('/api/admin/config/affinity', json={'enabled': True})).status_code == 200
    assert (await admin.post(GRANT, headers={'Idempotency-Key': new_key})).status_code == 200
    assert cards(settings.database_path, 'producer') == 2
    assert cards(settings.database_path, 'later') == 1


async def test_concurrent_same_id_once_and_new_id_is_new_grant(make_client, github, settings):
    admin = await admin_client(make_client, github, clock=FakeClock())
    key = str(uuid4())
    responses = await asyncio.gather(*[admin.post(GRANT, headers={'Idempotency-Key': key}) for _ in range(3)])
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert all(r.json() == responses[0].json() for r in responses)
    assert cards(settings.database_path, 'boss') == 1
    assert (await admin.post(GRANT, headers={'Idempotency-Key': str(uuid4())})).status_code == 200
    assert cards(settings.database_path, 'boss') == 2


async def test_invalid_keys_have_no_effect_and_legacy_calls_are_distinct(make_client, github, settings):
    from tests.test_sc_play_scope import gameplay_snapshot
    admin = await admin_client(make_client, github, clock=FakeClock())
    before = gameplay_snapshot(settings.database_path)
    for header in ('', 'null', 'sc', 'a' * 500, str(uuid4()).upper(), str(uuid4()) + ',' + str(uuid4())):
        response = await admin.post(GRANT, headers={'Idempotency-Key': header})
        assert response.status_code == 400, (header, response.text)
        assert gameplay_snapshot(settings.database_path) == before, header
    for header in ([('Idempotency-Key', str(uuid4())), ('Idempotency-Key', str(uuid4()))],):
        response = await admin.post(GRANT, headers=header)
        assert response.status_code == 400, (header, response.text)
        assert gameplay_snapshot(settings.database_path) == before, header
    for attempt in range(2):
        response = await admin.post(GRANT)
        assert response.status_code == 200, (attempt, response.text)
    assert cards(settings.database_path, 'boss') == 2
    with sqlite3.connect(settings.database_path) as db:
        ids = db.execute('SELECT operation_id FROM reset_card_grants').fetchall()
    assert len(ids) == len(set(ids)) == 2
    assert all(key for (key,) in ids)


async def test_result_insert_failure_rolls_back_all_grant_effects(make_client, github, settings):
    from tests.test_sc_play_scope import gameplay_snapshot
    admin = await admin_client(make_client, github, clock=FakeClock())
    before = gameplay_snapshot(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("CREATE TRIGGER injected_grant_failure BEFORE INSERT ON reset_card_grants BEGIN SELECT RAISE(ABORT,'injected grant failure'); END")
    key = str(uuid4())
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        await admin.post(GRANT, headers={'Idempotency-Key': key})
    assert gameplay_snapshot(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as db:
        db.execute('DROP TRIGGER injected_grant_failure')
    response = await admin.post(GRANT, headers={'Idempotency-Key': key})
    assert response.status_code == 200, response.text
    assert cards(settings.database_path, 'boss') == 1


async def test_replay_is_persistent_and_still_requires_admin_origin_and_sc(make_client, github, settings):
    import subprocess
    import sys
    from tests.test_sc_play_scope import gameplay_snapshot
    clock = FakeClock()
    admin = await admin_client(make_client, github, clock=clock)
    user = make_client(FakeLLM(), clock=clock)
    key = str(uuid4())
    first = await admin.post(GRANT, headers={'Idempotency-Key': key})
    assert first.status_code == 200, first.text
    before = gameplay_snapshot(settings.database_path)
    for client, headers, expected in (
        (user, {'Idempotency-Key': key}, 404),
        (admin, {'Idempotency-Key': key, 'Origin': 'https://other.invalid'}, 403),
        (admin, {'Idempotency-Key': key, 'X-Work-ID': 'demo'}, 409),
    ):
        response = await client.post(GRANT, headers=headers)
        assert response.status_code == expected, (headers, response.text)
        assert gameplay_snapshot(settings.database_path) == before, headers
    # A new OS process opens the same DB, with gifts closed and no runtime caches.
    code = """
import asyncio, json, sys
from pathlib import Path
from eidolon.data_access import Database
from eidolon.gifts import Gifts
from eidolon.affinity import Affinities
from tests.fakes import FakeClock, FakeRandom
async def main():
    db = Database(Path(sys.argv[1]))
    try:
        gifts = Gifts(db, FakeClock(), lambda: False, Affinities(db,FakeClock(),lambda:False), {}, {}, FakeRandom())
        print(json.dumps((await gifts.grant_reset_cards(sys.argv[2])).shown()))
    finally:
        await db.close()
asyncio.run(main())
"""
    # Opening Gifts notices the off switch; take that known configuration change out
    # of the outcome comparison. Replay itself must not mutate balances or history.
    result = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, str(settings.database_path), key], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    import json
    assert json.loads(result.stdout) == first.json()
    after = gameplay_snapshot(settings.database_path)
    assert {k: v for k, v in after.items() if k != 'gifts_opened'} == {k: v for k, v in before.items() if k != 'gifts_opened'}


async def test_cancelled_wait_commits_once_and_distinct_concurrent_ids_each_grant(
    make_client, github, settings, monkeypatch,
):
    import threading
    import eidolon.gifts
    admin = await admin_client(make_client, github, clock=FakeClock())
    entered, release = threading.Event(), threading.Event()
    original = eidolon.gifts._grant_reset_cards
    def blocked(db, now, operation_id):
        entered.set()
        if not release.wait(5):
            raise RuntimeError('test did not release grant transaction')
        return original(db, now, operation_id)
    key = str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(eidolon.gifts, '_grant_reset_cards', blocked)
        pending = asyncio.create_task(admin.post(GRANT, headers={'Idempotency-Key': key}))
        try:
            assert await asyncio.to_thread(entered.wait, 2), 'grant did not enter transaction'
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            release.set()
        replay = await admin.post(GRANT, headers={'Idempotency-Key': key})
        assert replay.status_code == 200, replay.text
    assert cards(settings.database_path, 'boss') == 1
    results = await asyncio.gather(*[
        admin.post(GRANT, headers={'Idempotency-Key': str(uuid4())}) for _ in range(2)
    ])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert cards(settings.database_path, 'boss') == 3
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT count(*) FROM reset_card_grants').fetchone() == (3,)
