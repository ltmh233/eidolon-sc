"""Complete-field differential against the original Python display implementation."""
import json
import random
import sqlite3
import time

from tests.fakes import FakeLLM
from tests.test_affinity_snapshot import settings as settings
from tests.test_go_stats import gateway_for
from tests.test_text_turn import start_conversation


async def test_complete_fields_match_python_oracle(make_client, settings, monkeypatch):
    from eidolon.delivery_cues import CueFilter
    from eidolon.store import Message, ReachOut
    seed = 20261029
    random_source = random.Random(seed)
    whitespace = ''.join(chr(c) for c in [*range(9, 14), *range(28, 33), 0x85, 0xa0, 0x1680, *range(0x2000, 0x200b), 0x2028, 0x2029, 0x202f, 0x205f, 0x3000])
    fields = [' A [x] B ', '[open <soft>x</soft>', '(hello]world', '[a(b]c)d', '<Whatever>hello</Whatever>', '', '[cue]', '\u200b [x] \ufeff', 'e\u0301🙂', r'\u200b [x] \ufeff e\u0301', whitespace + 'visible' + whitespace]
    for size in (79, 80, 81):
        fields.extend(['[' + '🙂' * (size - 1), '[' + '🙂' * (size - 1) + ']hello', '[' + '🙂' * (size - 1) + '<soft>x</soft>'])
    tokens = ['[', ']', '(', ')', '（', '）', '【', '】', '<', '>', '🙂', '中', 'e\u0301', '\u200b', '\ufeff', *whitespace]
    fields += [''.join(random_source.choice(tokens) for _ in range(random_source.randrange(220))) for _ in range(2000)]
    # Actual filtering of a large character field, beyond the existing user bypass.
    fields.append(' [cue] ' + '大🙂 ' * 500000 + ' [tail] ')
    client = make_client(FakeLLM())
    conversation = await start_conversation(client)
    with sqlite3.connect(settings.database_path) as db:
        for index, field in enumerate(fields):
            segments = [{'ja': field, 'zh': '[cue]'}, {'ja': '[open', 'zh': 'close]'}]
            db.execute("INSERT INTO messages(conversation_id,role,ja,zh,segments,created_at) VALUES (?,'character',?,?,?,?)", (conversation, field, fields[(index + 1) % len(fields)], json.dumps(segments), '2026-10-29T00:00:00Z'))
        db.execute("UPDATE messages SET text='[character text] retained' WHERE conversation_id=? AND id=(SELECT min(id) FROM messages WHERE conversation_id=?)", (conversation, conversation))
        for role, text, ja, zh, segments in (
            ('character', None, None, '', [{'ja':'[cue]', 'zh':'值'}]),
            ('character', None, '', None, []),
            ('user', '[user text]', '[user ja]', '<soft>user zh</soft>', [{'ja':'[user segment]', 'zh':'<soft>user segment zh</soft>'}]),
        ):
            db.execute('INSERT INTO messages(conversation_id,role,text,ja,zh,segments,created_at) VALUES (?,?,?,?,?,?,?)', (conversation, role, text, ja, zh, json.dumps(segments), '2026-10-29T00:00:00Z'))
        raw_before = db.execute('SELECT ja,zh,segments FROM messages WHERE conversation_id=? ORDER BY id', (conversation,)).fetchall()
    path = f'/api/conversations/{conversation}/messages'
    started = time.perf_counter()
    async with gateway_for(client, settings, binary_env='EIDOLON_TIMELINE_BINARY', token_flag='-timeline-token-file') as (gateway, app):
        expected = await client.get(path)
        assert expected.status_code == 200
        def unavailable(*args):
            raise RuntimeError('Python display calculation unavailable in v2 test window')
        with monkeypatch.context() as patch:
            patch.setattr(Message, 'without_cues', unavailable)
            patch.setattr(ReachOut, 'without_cues', unavailable)
            actual = await gateway.get(path, headers={'X-Core-Display-Text': 'client-forged'})
        assert actual.status_code == 200
        shown, oracle = actual.json(), expected.json()
        assert len(shown) == len(oracle) == len(fields) + 3
        for index, (got, want) in enumerate(zip(shown, oracle, strict=True)):
            assert got == want, (seed, index, 'complete field mismatch')
            # This independently establishes oracle use of exactly one feed + finish.
            if index < len(fields):
                line = CueFilter()
                assert got['ja'] == line.feed(fields[index])[0] + line.finish()[0], (seed, index)
        assert shown[0]['text'] == '[character text] retained'
        assert shown[-3]['ja'] is None and shown[-3]['zh'] == '' and shown[-2]['ja'] == '' and shown[-2]['zh'] is None
        assert shown[-1]['text'] == '[user text]' and shown[-1]['ja'] == '[user ja]' and shown[-1]['zh'] == '<soft>user zh</soft>'
        assert shown[-1]['segments'] == [{'ja':'[user segment]', 'zh':'<soft>user segment zh</soft>'}]
        assert shown[5]['zh'] == '', 'cue-only zh stays empty so the frontend may fall back to ja'
        with sqlite3.connect(settings.database_path) as db:
            retained = db.execute('SELECT ja,zh,segments FROM messages WHERE conversation_id=? ORDER BY id', (conversation,)).fetchall()
        assert retained == raw_before, 'all original SQL language and segment fields remain unchanged'
        assert [row[0] for row in retained[:len(fields)]] == fields
        print('UNICODE codepoints=' + ','.join(f'{ord(c):04x}' for c in '\u200b\ufeffe\u0301'))
        print(f'PASS visibleText Python single-feed+finish oracle seed={seed}, fields={len(fields)}, large_character_bytes={len(fields[-1].encode())}, seconds={time.perf_counter()-started:.3f}')
