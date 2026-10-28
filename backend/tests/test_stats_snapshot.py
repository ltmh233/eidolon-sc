"""Only authenticated core consumers can receive the SC statistics metadata stream."""
import json

from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.fakes import FakeClock, FakeLLM
from tests.test_text_turn import start_conversation, send_turn


async def test_stats_metadata_is_private_raw_and_user_bound(make_client, settings):
    clock = FakeClock()
    client = make_client(FakeLLM(["<ja>はい</ja><zh>好</zh>"]), clock=clock)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, 'PRIVATE_BODY_MUST_NOT_LEAK')
    path = '/api/_core/stats'
    assert (await client.get(path)).status_code == 404
    denied = await client.get(path, headers={'Authorization': 'Bearer ' + TOKEN, 'X-Work-ID': 'demo'})
    assert denied.status_code == 409
    response = await client.get(path + '?user_id=other&range=30d', headers={'Authorization': 'Bearer ' + TOKEN})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    records = [json.loads(line) for line in response.text.splitlines()]
    assert records[0][:4] == ['meta', 1, 'sc', '30d']
    assert records[-1] == ['end']
    assert any(row[0] == 'contact' and row[2] == 'turn' for row in records)
    assert 'PRIVATE_BODY_MUST_NOT_LEAK' not in response.text
    assert 'days_known' not in response.text and 'visits' not in response.text
    other = make_client(FakeLLM(), 'other', clock=clock)
    response = await other.get(path, headers={'Authorization': 'Bearer ' + TOKEN})
    assert response.status_code == 200
    assert not any(json.loads(line)[0] == 'contact' for line in response.text.splitlines())
