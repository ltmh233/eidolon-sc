"""Real Go summary selection/projection, preserving indexed reads and title waiting."""
import asyncio
from dataclasses import replace
import sqlite3
import time

import pytest

from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.test_go_stats import gateway_for
from tests.test_delete_account import user_id
from tests.test_timeline_snapshot import reached
from tests.test_unseen_reach_outs import seen_in
from tests.fakes import FakeClock, FakeLLM, FakeBackgroundLLM
from tests.test_text_turn import start_conversation, send_turn
from tests.test_sc_play_scope import gameplay_snapshot


async def test_go_summaries_match_legacy_without_seen_or_foreign_data(make_client, settings, monkeypatch):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    other = make_client(FakeLLM(), 'other', clock=clock)
    first = await start_conversation(client)
    second = await start_conversation(client)
    empty = await start_conversation(client)
    kogane = await start_conversation(client, 'kogane')
    foreign = await start_conversation(other)
    at = clock.now.isoformat()
    reached(settings.database_path, second, clock.now, '[shyly] はい')
    reached(settings.database_path, foreign, clock.now, 'FOREIGN_SECRET')
    with sqlite3.connect(settings.database_path) as db:
        own = user_id(settings.database_path, 'producer')
        db.execute("INSERT INTO work_scopes VALUES ('demo')")
        for identity, work, character in (('demo','demo','mano'), ('removed','sc','retired')):
            db.execute('INSERT INTO conversations(id,user_id,work_id,character_id,created_at) VALUES (?,?,?,?,?)', (identity, own, work, character, at))
        db.execute("INSERT INTO messages(conversation_id,role,text,created_at) VALUES ('demo','user','DEMO_SECRET',?)", (at,))
        db.execute('UPDATE conversations SET created_at=?', (at,))
        for conversation in (first, second):
            db.execute("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user','user [unchanged]',?)", (conversation, at))
            reply = db.execute("INSERT INTO messages(conversation_id,role,ja,zh,created_at) VALUES (?,'character','[shyly] 返事','<soft>回复</soft>',?)", (conversation, at)).lastrowid
            db.execute("INSERT INTO photos(id,conversation_id,message_id,tag,status,created_at,updated_at) VALUES (?,?,?,'PRIVATE_TAG','ready',?,?)", ('photo-'+conversation, conversation, reply, at, at))
        for kind in ('gift', 'hung_up', 'ended'):
            db.execute('INSERT INTO conversation_events(conversation_id,kind,created_at,gift_id,liked) VALUES (?,?,?,?,1)', (second, kind, at, 'cake' if kind=='gift' else None))
        db.execute("INSERT INTO conversation_events(conversation_id,kind,created_at,gift_id) VALUES (?,'gift',?,'cake')", (kogane, at))
    before = gameplay_snapshot(settings.database_path)
    async with gateway_for(client, settings, binary_env='EIDOLON_SUMMARIES_BINARY', token_flag='-summaries-token-file') as (gateway, app):
        async def compare(path, label, **kwargs):
            expected = await client.get(path, **kwargs)
            got = await gateway.get(path, **kwargs)
            assert got.status_code == expected.status_code, (label, got.text)
            assert got.json() == expected.json(), (label, got.text, expected.text)
            assert 'PRIVATE_TAG' not in got.text and 'FOREIGN_SECRET' not in got.text and 'DEMO_SECRET' not in got.text, label
            return got
        for character in ('mano', 'kogane', 'retired', 'missing', ''):
            await compare('/api/conversations', ('character', character), params={'character_id': character})
        await compare('/api/conversations/latest', 'latest')
        await compare('/api/conversations', 'required query missing')
        await compare('/api/conversations', 'duplicate character query', params=[('character_id','kogane'),('character_id','mano')])
        await compare('/api/conversations', 'forged user', params={'character_id':'mano','user_id':user_id(settings.database_path, 'other')})
        for label, message_at, event_at, reach_at in (
            ('space ISO separator', '2026-10-28 11:00:00+00:00', '2026-10-28 12:00:00+00:00', '2026-10-28 12:00:00+00:00'),
            ('sub-microsecond ties truncate in Python', '2026-10-28T11:00:00Z', '2026-10-28T12:00:00.000000001Z', '2026-10-28T12:00:00.000000000Z'),
        ):
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE messages SET created_at=? WHERE conversation_id=?', (message_at, second))
                db.execute('UPDATE conversation_events SET created_at=? WHERE conversation_id=?', (event_at, second))
                db.execute('UPDATE reach_outs SET created_at=? WHERE conversation_id=?', (reach_at, second))
            await compare('/api/conversations', label, params={'character_id':'mano'})
        with sqlite3.connect(settings.database_path) as db:
            for table in ('messages', 'conversation_events', 'reach_outs'):
                db.execute(f'UPDATE {table} SET created_at=? WHERE conversation_id=?', (at, second))
        before = gameplay_snapshot(settings.database_path)
        for route, params in (('/api/conversations', {'character_id':'mano'}), ('/api/conversations/latest', {})):
            for scope in ({'X-Work-ID':'demo'}, {'X-Work-ID':'sc, demo'}):
                denied = await gateway.get(route, params=params, headers=scope)
                assert denied.status_code == 409, (route, scope, denied.text)
        assert gameplay_snapshot(settings.database_path) == before, 'summary/invalid scopes changed business data'
        assert seen_in(settings.database_path, second) == [False]
        assert seen_in(settings.database_path, foreign) == [False]
        # Inject failure into the old record chooser; private candidates and Go remain live.
        import eidolon.store
        with monkeypatch.context() as patch:
            def broken(*args):
                raise RuntimeError('public Python preview selection unavailable')
            patch.setattr(eidolon.store, '_last', broken)
            assert (await gateway.get('/api/conversations', params={'character_id':'mano'})).status_code == 200
            assert (await gateway.get('/api/conversations/latest')).status_code == 200
        expected_without_cues = (await client.get('/api/conversations', params={'character_id':'mano'})).json()
        with monkeypatch.context() as patch:
            patch.setattr(eidolon.store.Message, 'without_cues', broken)
            patch.setattr(eidolon.store.ReachOut, 'without_cues', broken)
            assert (await gateway.get('/api/conversations', params={'character_id':'mano'})).json() == expected_without_cues
            with pytest.raises(RuntimeError):
                await client.get('/api/conversations', params={'character_id':'mano'})
        # Photo preview is only a ready boolean; no IDs, failure reasons or tags appear.
        for status in ('pending','failed','ready'):
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE photos SET status=? WHERE conversation_id=?', (status, first))
            await compare('/api/conversations', ('photo',status), params={'character_id':'mano'})
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE messages SET segments=? WHERE conversation_id=? AND role='character'", ('[{"ja":null,"zh":"broken"}]', second))
        await compare('/api/conversations', 'unselected malformed cue segment remains hidden', params={'character_id':'mano'})
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE messages SET segments='[]' WHERE conversation_id=?", (second,))
            db.execute("UPDATE messages SET segments=? WHERE conversation_id=? AND role='character'", ('[{"ja":null,"zh":"broken"}]', first))
        with pytest.raises(TypeError):
            await client.get('/api/conversations', params={'character_id':'mano'})
        failed = await gateway.get('/api/conversations', params={'character_id':'mano'})
        assert failed.status_code == 500 and failed.text == 'Internal Server Error'
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE messages SET segments='[]' WHERE conversation_id=?", (first,))
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE reach_outs SET segments=? WHERE conversation_id=?", ('[{"ja":null,"zh":"broken"}]', second))
        with pytest.raises(TypeError):
            await client.get('/api/conversations', params={'character_id':'mano'})
        failed = await gateway.get('/api/conversations', params={'character_id':'mano'})
        assert failed.status_code == 500 and failed.text == 'Internal Server Error'
        with sqlite3.connect(settings.database_path) as db:
            db.execute('UPDATE reach_outs SET created_at=? WHERE conversation_id=?', ('2020-01-01T00:00:00+00:00', second))
        await compare('/api/conversations', 'unselected malformed reach-out remains hidden', params={'character_id':'mano'})
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE reach_outs SET segments='[]',created_at=? WHERE conversation_id=?", (at, second))
        gift = app.state.backend.gift_catalog['cake']
        app.state.backend.gift_catalog['cake'] = replace(gift, retired=True)
        await compare('/api/conversations', 'retired gift', params={'character_id':'kogane'})
        # An unknown gift hidden by a same-time reach-out must not fail the request.
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE conversation_events SET gift_id='missing' WHERE conversation_id=? AND kind='gift'", (second,))
        await compare('/api/conversations', 'unselected unknown gift', params={'character_id':'mano'})
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE conversation_events SET gift_id='missing' WHERE conversation_id=? AND kind='gift'", (kogane,))
        with pytest.raises(KeyError):
            await client.get('/api/conversations', params={'character_id':'kogane'})
        failed = await gateway.get('/api/conversations', params={'character_id':'kogane'})
        assert failed.status_code == 500 and failed.text == 'Internal Server Error'
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE conversation_events SET gift_id='cake' WHERE gift_id='missing'")
            db.execute("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user',?,?)", (empty, '大预览🙂'*300000, at))
            db.executemany("INSERT INTO conversations(id,user_id,work_id,character_id,created_at) VALUES (?,?,'sc','mano',?)", [(f'long-{i}',own,at) for i in range(250)])
        started = time.perf_counter()
        await compare('/api/conversations', '250 extra conversations and 3.9MB preview', params={'character_id':'mano'})
        print(f'PASS Go summaries long-list/large-preview differential_seconds={time.perf_counter()-started:.3f}')
        for identity, original in (('other',other), ('producer',client)):
            gateway.cookies = {c.name:c.value for c in original.cookies.jar if c.value is not None}
            assert (await gateway.get('/api/conversations/latest')).json() == (await original.get('/api/conversations/latest')).json(), identity
        assert (await gateway.get('/api/_core/conversation-summaries/latest', headers={'Authorization':'Bearer '+TOKEN})).status_code == 404
        gateway.cookies.clear()
        assert (await gateway.get('/api/conversations/latest')).status_code == 401
        print('PASS Go summaries selection/projection, privacy, title/ended/unseen, scoped candidates and legacy edge cases')


async def test_lists_wait_for_only_the_original_users_title_task(make_client, settings, monkeypatch):
    gate = asyncio.Event()
    client = make_client(FakeLLM(*[['<ja>はい</ja><zh>好</zh>']]*3), background_llm=FakeBackgroundLLM('真实标题', gate=gate))
    other = make_client(FakeLLM(), 'other')
    conversation = await start_conversation(client)
    for i in range(3):
        assert (await send_turn(client, conversation, f'句{i}'))[-1]['type'] == 'done', i
    async with gateway_for(client, settings, binary_env='EIDOLON_SUMMARIES_BINARY', token_flag='-summaries-token-file') as (gateway, app):
        latest = await asyncio.wait_for(gateway.get('/api/conversations/latest'), 1)
        assert latest.status_code == 200 and not gate.is_set()
        gateway.cookies = {c.name:c.value for c in other.cookies.jar if c.value is not None}
        unrelated = await asyncio.wait_for(gateway.get('/api/conversations', params={'character_id':'mano'}), 1)
        assert unrelated.status_code == 200 and unrelated.json() == []
        gateway.cookies = {c.name:c.value for c in client.cookies.jar if c.value is not None}
        entered = asyncio.Event()
        original_settle = app.state.backend.titles.settle
        async def observe_settle(user):
            entered.set()
            await original_settle(user)
        with monkeypatch.context() as patch:
            patch.setattr(app.state.backend.titles, 'settle', observe_settle)
            pending = asyncio.create_task(gateway.get('/api/conversations', params={'character_id':'mano'}))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                assert not pending.done(), 'list returned while its title was still blocked'
                gate.set()
                response = await asyncio.wait_for(pending, 2)
                assert response.status_code == 200 and response.json()[0]['title'] == '真实标题'
            finally:
                gate.set()
                await asyncio.gather(pending, return_exceptions=True)
        print('PASS original per-user title wait and actual title completion; latest never waits')
