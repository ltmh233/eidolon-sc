"""Private capability/raw representation and deterministic errors before streaming."""
import json
import sqlite3

from types import SimpleNamespace

from eidolon import achievements
from tests.fakes import FakeLLM
from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.test_delete_account import user_id


async def test_raw_achievement_contract_and_known_read_error(make_client, settings):
    client = make_client(FakeLLM())
    uid = user_id(settings.database_path, 'producer')
    path = '/api/_core/achievements/mano'
    headers = {'Authorization': 'Bearer ' + TOKEN}
    for label, auth in (('missing credential', {}), ('wrong credential', {'Authorization': 'Bearer wrong'}),
                        ('duplicate credential', [('Authorization', 'Bearer ' + TOKEN), ('Authorization', 'Bearer wrong')])):
        assert (await client.get(path, headers=auth)).status_code == 404, label
    with sqlite3.connect(settings.database_path) as db:
        db.execute('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', (uid, 'turns', 'mano', 9007199254740993))
        db.execute('INSERT INTO achievement_progress VALUES (?,?,?,?,NULL)', (uid, 'old', 'mano', b'bad'))
        db.execute('INSERT INTO achievement_stages VALUES (?,?,?,?,?)', (uid, 'old', 'mano', 1.5, '20261029'))
    response = await client.get(path, headers=headers)
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/x-ndjson'
    assert response.headers['cache-control'] == 'no-store'
    assert rows[0] == ['meta', 1, 'sc', 'mano', True, 4, 2, 1, ['py311-sql-v1', 4300]]
    assert ['count', ['s', 'turns'], ['i', '9007199254740993']] in rows
    assert ['count', ['s', 'old'], ['b', 'YmFk']] in rows
    assert rows[-2:] == [['stage', ['s', 'old'], ['f', '3ff8000000000000'], ['2026-10-29T00:00:00', False, '1793232000000000']], ['end']]
    assert '/api/_core/achievements' not in (await client.get('/openapi.json')).text
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE achievement_stages SET unlocked_at='bad'")
    response = await client.get(path, headers=headers)
    assert response.status_code == 500
    assert response.json() == {'contract': 'sc-character-achievements-v1', 'error': 'legacy_read'}
    assert response.headers['cache-control'] == 'no-store'


async def test_unsupported_trusted_rule_shapes_are_explicit(make_client, settings, monkeypatch):
    client = make_client(FakeLLM())
    path = '/api/_core/achievements/mano'
    headers = {'Authorization': 'Bearer ' + TOKEN}
    invalid = [
        ('missing definitions', None),
        ('missing rule fields', (None,)),
        ('missing stages', (SimpleNamespace(id='turns', stages=None),)),
        ('missing stage fields', (SimpleNamespace(id='turns', stages=(None,)),)),
        ('boolean goal', (achievements.Achievement('turns', (achievements.Stage(True, 1),)),)),
        ('reserved identifier', (achievements.Achievement('complete', ()),)),
        ('unserializable goal', (achievements.Achievement('turns', (achievements.Stage(10 ** 4300, 1),)),)),
    ]
    for label, rules in invalid:
        with monkeypatch.context() as patch:
            patch.setattr(achievements, 'PER_CHARACTER', rules)
            response = await client.get(path, headers=headers)
            assert response.status_code == 503, label
            assert response.json() == {'contract': 'sc-character-achievements-v1', 'error': 'unsupported_contract'}, label
