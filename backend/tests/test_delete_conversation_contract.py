"""SC declarations and actual concurrent deletion at the public FastAPI seam."""
import asyncio

import httpx
from fastapi import FastAPI

from eidolon.store import Conversation
from tests.conftest import SITE
from tests.fakes import FakeLLM
from tests.test_reach_outs import backend_of
from tests.test_sc_play_scope import gameplay_snapshot
from tests.test_text_turn import start_conversation


async def test_delete_checks_every_declared_scope_without_reading_json(make_client,settings):
    client = make_client(FakeLLM())
    past = await start_conversation(client)
    await start_conversation(client)
    before = gameplay_snapshot(settings.database_path)
    cases = [({'X-Work-ID':value},[]) for value in ('demo','unknown','','SC',' sc ','null')]
    cases += [({},[('work_id',value)]) for value in ('demo','','SC')]
    cases += [([('X-Work-ID','sc'),('X-Work-ID','demo')],[]),
              ({},[('work_id','sc'),('work_id','demo')]),
              ({'X-Work-ID':'sc'},[('work_id','demo')])]
    for headers,query in cases:
        for attempt in range(2):
            response = await client.delete('/api/conversations/'+past,headers=headers,params=query)
            assert gameplay_snapshot(settings.database_path) == before,(headers,query,attempt,response.status_code)
            assert response.status_code == 409,(headers,query,attempt,response.text)
    # This API has no JSON request model. Keep that protocol even for malformed JSON.
    response = await client.request('DELETE','/api/conversations/'+past,params={'work_id':'sc'},
                                    headers={'X-Work-ID':'sc','Content-Type':'application/json'},content=b'{not-json')
    assert response.status_code == 204 and response.content == b''


async def test_two_pre_read_deletes_use_real_transactions_and_return_204_then_404(make_client,settings,monkeypatch):
    original = make_client(FakeLLM())
    past = await start_conversation(original)
    current = await start_conversation(original)
    backend = backend_of(original)
    real_delete = backend.store.delete_conversation
    arrived = 0
    both_read,release = asyncio.Event(),asyncio.Event()
    async def after_pre_read(conversation: Conversation):
        nonlocal arrived
        assert conversation.id == past
        arrived += 1
        if arrived == 2:
            both_read.set()
        await release.wait()
        await real_delete(conversation)
    monkeypatch.setattr(backend.store,'delete_conversation',after_pre_read)
    assert isinstance(original._transport,httpx.ASGITransport)
    app = original._transport.app
    assert isinstance(app,FastAPI)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),
                                base_url='http://test',headers=SITE,cookies=original.cookies) as client:
        requests = [asyncio.create_task(client.delete('/api/conversations/'+past)) for _ in range(2)]
        try:
            await asyncio.wait_for(both_read.wait(),2)
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*requests),3)
            assert sorted(r.status_code for r in responses) == [204,404],[r.status_code for r in responses]
            absent = next(r for r in responses if r.status_code == 404)
            assert absent.json() == {'detail':'对话不存在'}
            assert next(r for r in responses if r.status_code == 204).content == b''
            assert await backend.store.get_conversation(past) is None
            assert await backend.store.get_conversation(current) is not None
        finally:
            release.set()
            for request in requests:
                if not request.done():
                    request.cancel()
            await asyncio.gather(*requests,return_exceptions=True)
