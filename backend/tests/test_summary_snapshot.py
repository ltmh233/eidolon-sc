"""Private summary candidates are user-scoped and never mark reach-outs seen."""
import json

from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.fakes import FakeClock, FakeLLM
from tests.test_text_turn import start_conversation
from tests.test_timeline_snapshot import reached
from tests.test_unseen_reach_outs import seen_in


async def test_raw_summary_candidates_are_private_and_leave_seen_unchanged(make_client, settings):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    other = make_client(FakeLLM(), 'other', clock=clock)
    conversation = await start_conversation(client)
    foreign = await start_conversation(other)
    reached(settings.database_path, conversation, clock.now)
    reached(settings.database_path, foreign, clock.now, 'FOREIGN_BODY')
    path = '/api/_core/conversation-summaries'
    assert (await client.get(path, params={'character_id': 'mano'})).status_code == 404
    response = await client.get(path, params={'character_id': 'mano', 'user_id': 'other'}, headers={'Authorization': 'Bearer ' + TOKEN})
    assert response.status_code == 200, response.text
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert rows[0][:4] == ['meta', 1, 'sc', 'list'] and rows[-1] == ['end']
    assert [row[1]['id'] for row in rows[1:-1]] == [conversation]
    assert 'FOREIGN_BODY' not in response.text and 'last_message' not in response.text
    assert seen_in(settings.database_path, conversation) == [False]
    assert seen_in(settings.database_path, foreign) == [False]
