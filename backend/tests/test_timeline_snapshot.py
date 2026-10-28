"""Private timeline reading preserves the original seen watermark and ownership checks."""
import json
import sqlite3

from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.fakes import FakeClock, FakeLLM
from tests.test_text_turn import start_conversation
from tests.test_unseen_reach_outs import seen_in
from tests.test_reach_outs import backend_of


def reached(database, conversation, at, text='PRIVATE_REACH'):
    with sqlite3.connect(database) as db:
        return db.execute('INSERT INTO reach_outs(conversation_id,ja,zh,segments,created_at) VALUES (?,?,?,?,?)',
                          (conversation, text, '搭话', json.dumps([{'ja': text, 'zh': '搭话'}]), at.isoformat())).lastrowid


async def test_private_record_has_raw_groups_and_marks_only_captured_watermark(make_client, settings, monkeypatch):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    conversation = await start_conversation(client)
    reached(settings.database_path, conversation, clock.now)
    database = backend_of(client).database
    original_write = database.write
    inserted = False

    async def delayed_write(operation, *args, **kwargs):
        nonlocal inserted
        if operation.__name__ == '_see_reach_outs' and not inserted:
            reached(settings.database_path, conversation, clock.now, 'NEW_AFTER_READ')
            inserted = True
        return await original_write(operation, *args, **kwargs)

    path = f'/api/_core/conversations/{conversation}/messages'
    assert (await client.get(path)).status_code == 404
    assert seen_in(settings.database_path, conversation) == [False]
    with monkeypatch.context() as patch:
        patch.setattr(database, 'write', delayed_write)
        response = await client.get(path, headers={'Authorization': 'Bearer ' + TOKEN})
    assert response.status_code == 200, response.text
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert rows[0] == ['meta', 1, 'sc', conversation] and rows[-1] == ['end']
    assert sum(row[0] == 'reach_out' for row in rows) == 1
    assert 'NEW_AFTER_READ' not in response.text
    assert seen_in(settings.database_path, conversation) == [True, False]
