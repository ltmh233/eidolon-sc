"""Real old/new Core and Python combinations; selected by explicit test environment."""
import asyncio
import json
import os
import sqlite3

import pytest

from tests.fakes import FakeClock, FakeLLM, FakeBackgroundLLM
from tests.test_affinity_snapshot import settings as settings
from tests.test_go_stats import gateway_for
from tests.test_text_turn import start_conversation, send_turn
from tests.test_timeline_snapshot import reached
from tests.test_unseen_reach_outs import seen_in
from tests.test_reach_outs import backend_of


async def test_real_display_version_combination(make_client, settings, monkeypatch):
    combination = os.environ.get('EIDOLON_DISPLAY_COMBINATION')
    if not combination:
        pytest.skip('set EIDOLON_DISPLAY_COMBINATION and EIDOLON_VERSION_BINARY for version acceptance')
    assert combination in ('old-old', 'old-new', 'new-new', 'new-old', 'native-off')
    title_gate = asyncio.Event()
    client = make_client(FakeLLM(*[['<ja>はい</ja><zh>好</zh>']] * 3), background_llm=FakeBackgroundLLM('版本真实标题', gate=title_gate))
    conversation = await start_conversation(client)
    for index in range(3):
        assert (await send_turn(client, conversation, f'触发标题{index}'))[-1]['type'] == 'done', index
    reached(settings.database_path, conversation, FakeClock().now, '[PRIVATE_VERSION_CUE] visible')
    app = client._transport.app
    captured = []
    async def observed(scope, receive, send):
        if scope['type'] == 'http':
            captured.append(scope['path'])
        await app(scope, receive, send)
    flag = '-stats-token-file' if combination == 'native-off' else '-timeline-token-file'
    extra = () if combination == 'native-off' else ('-summaries-token-file', str(settings.core_service_token_path))
    async with gateway_for(client, settings, binary_env='EIDOLON_VERSION_BINARY', token_flag=flag, extra_args=extra, app_override=observed) as (gateway, _):
        path = f'/api/conversations/{conversation}/messages'
        got = await gateway.get(path, headers={'X-Core-Display-Text': 'client-forged'})
        expected_status = 502 if combination == 'new-old' else 200
        assert got.status_code == expected_status, (combination, got.status_code)
        assert 'PRIVATE_VERSION_CUE' not in got.text
        assert seen_in(settings.database_path, conversation) == [True], 'even new Core+old Python may already capture seen'
        assert len(captured) == 1, (combination, captured)
        if expected_status == 200:
            assert got.json() == (await client.get(path)).json()
        backend = backend_of(client)
        entered = asyncio.Event()
        original_settle = backend.titles.settle
        async def observe_settle(user):
            entered.set()
            await original_settle(user)
        with monkeypatch.context() as patch:
            patch.setattr(backend.titles, 'settle', observe_settle)
            captured.clear()
            pending = asyncio.create_task(gateway.get('/api/conversations', params={'character_id':'mano'}))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                assert not pending.done(), 'old Python list still waits for original title task before 502'
                title_gate.set()
                summary = await asyncio.wait_for(pending, 3)
            finally:
                title_gate.set()
                await asyncio.gather(pending, return_exceptions=True)
        with sqlite3.connect(settings.database_path) as db:
            assert db.execute('SELECT title FROM conversations WHERE id=?', (conversation,)).fetchone()[0] == '版本真实标题'
        assert summary.status_code == expected_status and 'PRIVATE_VERSION_CUE' not in summary.text
        assert len(captured) == 1, (combination, captured)
        # Historical malformed segments terminate the old private stream: Core 502.
        with sqlite3.connect(settings.database_path) as db:
            db.execute("INSERT INTO messages(conversation_id,role,ja,zh,segments,created_at) VALUES (?,'character','ja','zh',?,?)", (conversation, json.dumps([{'ja':None,'zh':'bad'}]), FakeClock().now.isoformat()))
        with pytest.raises(TypeError):
            await client.get(path)
        if combination != 'native-off':
            captured.clear()
            malformed = await gateway.get(path)
            assert malformed.status_code == 502 and len(captured) == 1, (combination, malformed.status_code)
        print(f'PASS combination={combination}, history={got.status_code}, list={summary.status_code}, seen=True, real_title_persisted=True, one_read_per_request=True')
