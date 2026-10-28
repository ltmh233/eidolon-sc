"""SC creation boundaries use the real API and synthetic SQLite."""
import json
from tests.fakes import FakeLLM
from tests.test_sc_play_scope import gameplay_snapshot


async def test_direct_creation_rejects_all_scope_channels_before_business_write(make_client, settings):
    client = make_client(FakeLLM())
    before = gameplay_snapshot(settings.database_path)
    cases = [({'character_id':'mano','work_id':work},{},[]) for work in ('demo',None,'SC',' sc ',1,False,[],{})]
    cases += [({'character_id':'mano'}, {'X-Work-ID':'demo'}, []),
              ({'character_id':'mano'}, {}, [('work_id','sc'),('work_id','demo')]),
              ({'character_id':'mano','work_id':'demo'}, {'X-Work-ID':'sc'}, []),
              ({'character_id':'mano','requestData':{'work_id':None}}, {}, [])]
    for body, headers, params in cases:
        for attempt in range(2):
            response = await client.post('/api/conversations',json=body,headers=headers,params=params)
            assert response.status_code == 409, (body,headers,params,attempt,response.text)
            assert gameplay_snapshot(settings.database_path) == before, (body,headers,params,attempt)
    for label,body in (
        ('flat duplicate','{"character_id":"mano","work_id":"demo","work_id":"sc"}'),
        ('nested duplicate','{"character_id":"mano","requestData":{"work_id":"demo","work_id":"sc"}}'),
    ):
        response = await client.post('/api/conversations',content=body,headers={'Content-Type':'application/json'})
        assert response.status_code == 409, (label,response.text)
        assert gameplay_snapshot(settings.database_path) == before,label


async def test_original_creation_keeps_extra_fields_and_new_intents(make_client):
    client = make_client(FakeLLM())
    first = await client.post('/api/conversations', json={'character_id':'mano', 'extra':123})
    second = await client.post('/api/conversations', json={'character_id':'mano', 'work_id':'sc'})
    assert first.status_code == second.status_code == 201
    assert set(first.json()) == {'id','user_id','character_id','created_at','title'}
    assert first.json()['id'] != second.json()['id']
    assert first.json()['title'] is None
    listed = await client.get('/api/conversations', params={'character_id':'mano'})
    assert [c['id'] for c in listed.json()] == [second.json()['id'], first.json()['id']]
    assert json.loads(first.content)['character_id'] == 'mano'
