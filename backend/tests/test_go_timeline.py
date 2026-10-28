"""Real Go history projection: ordering, privacy, captured seen state and large messages."""
from dataclasses import replace
import json
import sqlite3
import time
import pytest

from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.test_go_stats import gateway_for
from tests.fakes import FakeClock, FakeLLM
from tests.test_text_turn import start_conversation
from tests.test_timeline_snapshot import reached
from tests.test_unseen_reach_outs import seen_in
from tests.test_delete_account import user_id


async def test_native_history_matches_legacy_and_cannot_read_foreign_state(make_client, settings, monkeypatch):
    clock = FakeClock()
    original = make_client(FakeLLM(), clock=clock)
    other = make_client(FakeLLM(), 'other', clock=clock)
    conversation = await start_conversation(original)
    foreign = await start_conversation(other)
    at = clock.now.isoformat()
    reached(settings.database_path, conversation, clock.now, '[shyly] はい')
    reached(settings.database_path, foreign, clock.now)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO work_scopes VALUES ('demo')")
        db.execute("INSERT INTO conversations(id,user_id,work_id,character_id,created_at) VALUES ('demo',?,'demo','mano',?)", (user_id(settings.database_path, 'producer'), at))
        db.execute("INSERT INTO conversations(id,user_id,work_id,character_id,created_at) VALUES ('removed',?,'sc','removed',?)", (user_id(settings.database_path, 'producer'), at))
        db.execute("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user','[user brackets untouched]',?)", (conversation, at))
        reply = db.execute("INSERT INTO messages(conversation_id,role,ja,zh,segments,created_at) VALUES (?,'character',?,?,?,?)",
                           (conversation, '[shyly] はい', '<soft>好的</soft>', json.dumps([{'ja': '[shyly] はい', 'zh': '<soft>好的</soft>'}]), at)).lastrowid
        db.execute("INSERT INTO photos(id,conversation_id,message_id,tag,status,created_at,updated_at) VALUES ('photo',?,?,?,'ready',?,?)", (conversation, reply, 'PRIVATE_PHOTO_TAG', at, at))
        db.execute("INSERT INTO conversation_events(conversation_id,kind,created_at,gift_id,liked) VALUES (?,'gift',?,'cake',1)", (conversation, at))
        db.execute("INSERT INTO conversation_events(conversation_id,kind,created_at) VALUES (?,'ended',?)", (conversation, at))
    reached(settings.database_path, 'removed', clock.now)
    path = f'/api/conversations/{conversation}/messages'
    async with gateway_for(original, settings, binary_env='EIDOLON_TIMELINE_BINARY', token_flag='-timeline-token-file') as (gateway, app):
        # Rejected requests must not mark even one foreign/hidden reach-out read.
        for target in (foreign, 'demo', 'removed', 'missing'):
            for boundary, client, prefix, headers in (
                ('gateway', gateway, '/api', {}),
                ('direct-private', original, '/api/_core', {'Authorization': 'Bearer ' + TOKEN}),
            ):
                response = await client.get(f'{prefix}/conversations/{target}/messages', headers=headers)
                assert response.status_code == 404, (boundary, target, response.text)
                assert seen_in(settings.database_path, foreign) == [False], (boundary, target)
                assert seen_in(settings.database_path, 'removed') == [False], (boundary, target)
        assert seen_in(settings.database_path, conversation) == [False]
        for headers, params in (({'X-Work-ID': 'demo'}, {}), ({}, {'work_id': 'demo'})):
            denied = await gateway.get(path, headers=headers, params=params)
            assert denied.status_code == 409, (headers, params)
            assert seen_in(settings.database_path, conversation) == [False], (headers, params)
        expected = await original.get(path)
        actual = await gateway.get(path, params={'user_id': user_id(settings.database_path, 'other')})
        assert actual.status_code == expected.status_code == 200
        assert actual.json() == expected.json(), 'mixed same-time history'
        for label, message_at, pending_at, reach_at in (
            ('space ISO history', '2026-10-28 12:00:00+00:00', '2026-10-28 12:00:00+00:00', '2026-10-28 12:00:00+00:00'),
            ('Python microsecond history ordering', '2026-10-28T12:00:00Z', '2026-10-28T12:00:00.000000001Z', '2026-10-28T12:00:00.000000000Z'),
        ):
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE messages SET created_at=? WHERE conversation_id=?', (message_at, conversation))
                db.execute('UPDATE conversation_events SET created_at=? WHERE conversation_id=?', (pending_at, conversation))
                db.execute('UPDATE reach_outs SET created_at=? WHERE conversation_id=?', (reach_at, conversation))
            got = await gateway.get(path)
            direct = await original.get(path)
            assert got.status_code == direct.status_code == 200, (label, got.text)
            assert got.json() == direct.json(), (label, got.text, direct.text)
            assert 'comparison_order' not in got.text, label
        with sqlite3.connect(settings.database_path) as db:
            for table in ('messages', 'conversation_events', 'reach_outs'):
                db.execute(f'UPDATE {table} SET created_at=? WHERE conversation_id=?', (at, conversation))
        assert 'PRIVATE_PHOTO_TAG' not in actual.text and 'liked' not in actual.text
        assert seen_in(settings.database_path, conversation) == [True]
        # Pending and failed photo states retain the original fields and optional shape.
        for status, reason in (('pending', None), ('failed', 'service_error')):
            with sqlite3.connect(settings.database_path) as db:
                db.execute('UPDATE photos SET status=?,reason=?', (status, reason))
            assert (await gateway.get(path)).json() == (await original.get(path)).json(), (status, reason)
        gift = app.state.backend.gift_catalog['cake']
        app.state.backend.gift_catalog['cake'] = replace(gift, retired=True)
        assert (await gateway.get(path)).json() == (await original.get(path)).json(), 'retired gift remains visible'
        from eidolon.store import Message, ConversationEvent, ReachOut
        with monkeypatch.context() as patch:
            def broken(*args):
                raise RuntimeError('public Python projection deliberately unavailable')
            for record_type in (Message, ConversationEvent, ReachOut):
                patch.setattr(record_type, 'shown', broken)
            assert (await gateway.get(path)).status_code == 200, 'Go must own projection'
        large_text = '大单条🙂' * 300000
        with sqlite3.connect(settings.database_path) as db:
            db.execute("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user',?,?)", (conversation, large_text, at))
            db.executemany("INSERT INTO conversation_events(conversation_id,kind,created_at) VALUES (?,'hung_up',?)", [(conversation, at)] * 10000)
        started = time.perf_counter()
        actual = await gateway.get(path)
        expected = await original.get(path)
        assert actual.status_code == expected.status_code == 200
        assert actual.json() == expected.json(), 'large message and 10000 events'
        print(f'PASS Go timeline large single UTF-8 message bytes={len(large_text.encode())}, 10000 extra events, differential_seconds={time.perf_counter()-started:.3f}')
        # Preserve the legacy unknown-gift HTTP failure without leaking the private marker.
        with sqlite3.connect(settings.database_path) as db:
            db.execute("UPDATE conversation_events SET gift_id='missing' WHERE kind='gift'")
        reached(settings.database_path, conversation, clock.now, 'LATE_REACH')
        with pytest.raises(KeyError):
            await original.get(path)
        failed = await gateway.get(path)
        assert failed.status_code == 500 and failed.text == 'Internal Server Error'
        assert 'unknown_gift' not in failed.text
        denied = await gateway.post(f'/api/_core/conversations/{conversation}/messages', headers={'Authorization': 'Bearer ' + TOKEN})
        assert denied.status_code == 404
        gateway.cookies.clear()
        denied = await gateway.get(path)
        assert denied.status_code == 401
        print('PASS Go mixed timeline/private projection, ownership/scope/seen, ended history, photo/gift compatibility')
