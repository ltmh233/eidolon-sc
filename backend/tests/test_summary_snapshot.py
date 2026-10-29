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


async def test_summary_raw_contract_rejects_before_titles_and_uses_structure(make_client, settings, monkeypatch):
    from eidolon.store import Message, ReachOut
    from tests.test_reach_outs import backend_of
    client = make_client(FakeLLM())
    conversation = await start_conversation(client)
    reached(settings.database_path, conversation, FakeClock().now, '[PRIVATE_CUE] raw')
    backend = backend_of(client)
    def unavailable(*args):
        raise RuntimeError('legacy text calculation unavailable')
    async def no_settle(*args):
        raise RuntimeError('must reject before title waiting')
    with monkeypatch.context() as patch:
        patch.setattr(backend.titles, 'settle', no_settle)
        for values in (['bad'], ['raw-fields-v1, raw-fields-v1'], ['raw-fields-v1', 'raw-fields-v1']):
            response = await client.get('/api/_core/conversation-summaries', params={'character_id': 'mano'}, headers=[('Authorization', 'Bearer ' + TOKEN)] + [('X-Core-Display-Text', value) for value in values])
            assert response.status_code == 409 and response.json() == {'detail': 'unsupported display text contract'}, values
    with monkeypatch.context() as patch:
        patch.setattr(Message, 'without_cues', unavailable)
        patch.setattr(ReachOut, 'without_cues', unavailable)
        response = await client.get('/api/_core/conversation-summaries/latest', headers={'Authorization': 'Bearer ' + TOKEN, 'X-Core-Display-Text': 'raw-fields-v1'})
        assert response.status_code == 200
        rows = [json.loads(line) for line in response.text.splitlines()]
        assert rows[0] == ['meta', 2, 'sc', 'latest', None, 'raw-fields-v1']
        assert response.headers['X-Core-Display-Text'] == 'raw-fields-v1'
        assert '[PRIVATE_CUE]' in response.text and 'segments' not in response.text
    assert seen_in(settings.database_path, conversation) == [False]


async def test_structural_display_errors_match_legacy_candidate_errors(make_client, settings):
    import sqlite3
    client = make_client(FakeLLM())
    conversation = await start_conversation(client)
    reached(settings.database_path, conversation, FakeClock().now)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO messages(conversation_id,role,ja,zh,created_at) VALUES (?,'character',NULL,'[cue]',?)", (conversation, FakeClock().now.isoformat()))
    for role in ('character', 'user'):
        for value in (None, 5, False, [], {}, '[cue] value'):
            segments = json.dumps([{'ja': value, 'zh': '值'}])
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE messages SET role=?,segments=? WHERE conversation_id=?', (role, segments, conversation))
                db.execute('UPDATE reach_outs SET segments=? WHERE conversation_id=?', (segments, conversation))
            result = []
            for raw in (False, True):
                headers = {'Authorization': 'Bearer ' + TOKEN}
                if raw:
                    headers['X-Core-Display-Text'] = 'raw-fields-v1'
                response = await client.get('/api/_core/conversation-summaries/latest', headers=headers)
                assert response.status_code == 200
                candidate = json.loads(response.text.splitlines()[1])[3]
                result.append((candidate['message']['display_error'], candidate['reach_out']['display_error']))
            assert result[0] == result[1], (role, value)
            assert result[1] == (role == 'character' and not isinstance(value, str), not isinstance(value, str)), (role, value)
