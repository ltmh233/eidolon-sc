"""Test-only Uvicorn process: real app/SQLite, fake external providers, cancellation evidence."""
import asyncio
import json
import os
import re
from pathlib import Path
import socket
import sys
import time
from datetime import timedelta
import sqlite3
import threading

import uvicorn

from eidolon.app import create_app
from eidolon.config import LLMSettings, Settings
from eidolon.work_schema import migrate
from eidolon.work_conversations import WorkConversations
from tests.test_work_memories import history
from tests.conftest import github_identity
from tests.fakes import FakeCatalog, FakeIdentityProvider, FakeLLM, FakeLLMService, FakeSpeech, FakeEmbedder, FakeReranker, FakeJudge, FakeClock, build_faked


async def run(directory: Path, snapshot: Path, display: Path, assets_url: str | None, *, listen_host: str = "127.0.0.1", listen_port: int = 0, stage_size: str | None = None) -> None:
    root = Path(__file__).resolve().parents[2]
    active_models = set()

    def model_event(kind, key):
        with (directory / 'model-events.jsonl').open('a') as file:
            file.write(json.dumps({'kind': kind, 'key': str(key), 'at': time.monotonic()}) + '\n')

    class ObservedLLM(FakeLLM):
        async def stream_with(self, key, messages, tools=(), *, forbid_calls=False):
            if any(m.role == "system" and "制作人喜欢咖啡" in m.content for m in messages):
                (directory / "memory-recalled").write_text("SC historical memory reached prompt")
            if any("DEMO_SECRET_MEMORY" in m.content for m in messages):
                (directory / "memory-leaked").write_text("unexpected foreign memory")
            if any(m.role == "user" and "cancel-stream" in m.content for m in messages):
                raw_marker = next(m.content for m in reversed(messages) if m.role == 'user' and 'cancel-stream' in m.content)
                matched = re.search(r'cancel-stream(?::stage-\d+-\d+)?', raw_marker)
                assert matched is not None
                marker = matched.group()  # log synthetic IDs only, never prompt/history text
                active_models.add(marker)
                model_event('start', marker)
                try:
                    yield "<ja>はい。</ja><zh>好的。</zh>"
                    (directory / "model-waiting").write_text("waiting in stream_with")
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    (directory / "cancelled.json").write_text(json.dumps({"at": time.monotonic()}))
                    model_event('cancel', marker)
                    raise
                finally:
                    active_models.discard(marker)
            else:
                async for chunk in super().stream_with(key, messages, tools, forbid_calls=forbid_calls):
                    yield chunk

    github = FakeIdentityProvider("github")
    code = github.will_identify(github_identity())
    stage_faults = os.environ.get('EIDOLON_STAGE_FAULTS') == '1'
    fault_code = github.will_identify(github_identity('1002', 'fault-user')) if stage_faults else None
    writer_code = github.will_identify(github_identity('1003', 'writer-user')) if stage_faults else None
    settings = Settings(
        database_path=directory / "test.db", characters_path=root / "characters/characters.toml",
        chat_llm=LLMSettings("http://llm.invalid/v1", "test-only", "test-model"),
        ice_servers=[], secret_key="three-process-test-only", dev_login=False,
        character_snapshot_path=snapshot, catalog_display_path=display,
        stand_assets_url=assets_url,
        admins=frozenset({("github", "1001")}), affinity_and_gifts=True,
        core_sc_create_token_path=(directory / "create-token") if (directory / "create-token").exists() else None,
        core_service_token_path=(directory / "core-token") if (directory / "core-token").exists() else None,
        core_gifts_panel_token_path=(directory / "panel-token") if (directory / "panel-token").exists() else None,
    )
    history(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO sign_in_methods VALUES ('u','github','1001','producer',NULL,'2020-01-01')")
    migration = migrate(settings.database_path, directory / "before.db", ("sc", "demo"))
    backend = build_faked(
        settings, ObservedLLM(*[["<ja>[PRIVATE_CHAIN_CUE] はい。</ja><zh><soft>好的。</soft></zh>"]] * 5),
        speech=FakeSpeech(), identity_providers={"github": github},
        own_llm_service=FakeLLMService(), catalog_transport=FakeCatalog().transport(),
        embedder=FakeEmbedder(), reranker=FakeReranker(), retrieval_judge=FakeJudge(),
        clock=FakeClock() if stage_size else None,
    )
    demo = WorkConversations(backend.database, user_id="u", work_id="demo")
    hidden = await demo.create("mano")
    await demo.append(hidden.id, "user", "other-work-private")
    from eidolon.memory import WorkMemories
    demo_memory = WorkMemories(backend.database, user_id="u", work_id="demo", characters={}, clock=FakeClock())
    await demo_memory.add("DEMO_SECRET_MEMORY", "mano", hidden.id)
    other_user = await backend.store.create_user("other")
    other = await backend.store.create_conversation(other_user.id, "mano")
    if stage_faults:
        await backend.database.write(lambda db: db.execute(
            "INSERT INTO sign_in_methods VALUES (?,'github','1002','fault-user',NULL,'2020-01-01')", (other_user.id,)).rowcount)
    if settings.core_service_token_path:
        await backend.database.write(lambda db: db.execute(
            "INSERT INTO affinities (user_id,character_id,value,updated_at,cooling) VALUES ('u','mano',0,?,0)",
            ((backend.clock() - timedelta(hours=12)).isoformat(),),
        ).rowcount)
    if settings.core_service_token_path:
        def achievement_facts(db):
            db.executemany("INSERT INTO achievement_progress VALUES ('u',?,'mano',?,NULL) ON CONFLICT(user_id,achievement,character_id) DO UPDATE SET count=excluded.count",
                           [('turns', 17), ('call_time', 1.9), ('tier', -1), ('complete', 0)])
            db.execute("INSERT INTO achievement_stages VALUES ('u','turns','mano',1,'20261029')")
        await backend.database.write(achievement_facts)
    if stage_size:
        from tests.stage_acceptance import HISTORY_SHAPES, SIZES, achievement_rows, history_text

        def stage_facts(db):
            db.executemany("INSERT INTO achievement_progress VALUES ('u',?,?,?,NULL)", achievement_rows(SIZES[stage_size]))
            for shape, count, length in HISTORY_SHAPES:
                conversation = 'stage-' + shape
                db.execute("INSERT INTO conversations (id,user_id,character_id,created_at,title,work_id) VALUES (?,'u','mano','2020-01-03T00:00:00+00:00',?,'sc')", (conversation, shape))
                db.executemany("INSERT INTO messages(conversation_id,role,text,created_at) VALUES (?,'user',?,'2020-01-03T00:00:00+00:00')", ((conversation, history_text(length)) for _ in range(count)))
            return {'achievement_rows': db.execute("SELECT count(*) FROM achievement_progress WHERE user_id='u'").fetchone()[0],
                    'history': db.execute("SELECT conversation_id,count(*),sum(length(text)),sum(length(cast(text as blob))) FROM messages WHERE conversation_id LIKE 'stage-%' GROUP BY conversation_id").fetchall()}

        seeded = await backend.database.write(stage_facts)
        (directory / 'seeded-sql.json').write_text(json.dumps(seeded, indent=2))
    app = create_app(backend)
    writer_release = threading.Event()
    original_submit = backend.database._submit
    blocker = None
    if stage_faults:
        original_state = backend.gifts.state

        def observed_submit(body, args, **kwargs):
            result = original_submit(body, args, **kwargs)
            if blocker is not None and getattr(body, '__name__', '') == '_state' and args[-1] is True:
                (directory / 'writer-state-queued').write_text(str(time.monotonic()))
            return result

        async def observed_state(*args):
            try:
                return await original_state(*args)
            except asyncio.CancelledError:
                (directory / 'writer-owner-cancelled').write_text(str(time.monotonic()))
                raise

        setattr(backend.database, '_submit', observed_submit)
        setattr(backend.gifts, 'state', observed_state)

    async def observe(scope, receive, send):
        # Pure ASGI pass-through: no BaseHTTPMiddleware stream/cancellation changes.
        if scope["type"] == "http":
            headers = {k.decode(): v.decode() for k, v in scope["headers"]}
            with (directory / "requests.jsonl").open("a") as f:
                f.write(json.dumps({"path": scope["path"], "scope": headers.get("x-work-id"),
                                   "origin": headers.get("origin"), "pid": os.getpid(),
                                   "asgi_spec": scope["asgi"]["spec_version"]}) + "\n")
        await app(scope, receive, send)

    listener = socket.socket()
    listener.bind((listen_host, listen_port))
    (directory / "backend.json").write_text(json.dumps({
        "port": listener.getsockname()[1], "pid": os.getpid(), "code": code,
        "history": "c", "hidden": hidden.id, "other": other.id, "migration": migration,
        "fault_code": fault_code, "fault_user": other_user.id if stage_faults else None,
        "writer_code": writer_code,
    }))
    async def observe_resources():
        nonlocal blocker
        while True:
            if stage_faults and (directory / 'auth-ban-arm').exists() and not (directory / 'auth-ban-ready').exists():
                # Synthetic in-flight identity state, retaining its session, just as
                # test_panel_inflight_identity_is_not_reauthenticated does locally.
                await backend.database.write(lambda db: db.execute('UPDATE users SET banned_at=? WHERE id=?',
                    (backend.clock().isoformat(), other_user.id)).rowcount)
                (directory / 'auth-ban-ready').write_text(str(time.monotonic()))
            if stage_faults and (directory / 'auth-unban-arm').exists() and not (directory / 'auth-unban-ready').exists():
                await backend.database.write(lambda db: db.execute('UPDATE users SET banned_at=NULL WHERE id=?', (other_user.id,)).rowcount)
                (directory / 'auth-unban-ready').write_text(str(time.monotonic()))
            if stage_faults and blocker is None and (directory / 'writer-block-arm').exists():
                def block(db):
                    (directory / 'writer-block-entered').write_text(str(time.monotonic()))
                    assert writer_release.wait(5), 'test writer gate must be released by its peer'
                blocker = original_submit(block, ())
            if stage_faults and (directory / 'writer-release').exists() and not (directory / 'writer-drained').exists():
                writer_release.set()
                assert blocker is not None
                await asyncio.wrap_future(blocker)
                await backend.database.write(lambda db: None)
                (directory / 'writer-drained').write_text(str(time.monotonic()))
            (directory / 'runtime.json').write_text(json.dumps({
                'at': time.monotonic(), 'tasks': len(asyncio.all_tasks()),
                'writer_queued': backend.database._tasks.qsize(),
                'readers_running': backend.database._reading,
                'readers_waiting': backend.database._waiting,
                'active_models': len(active_models),
            }))
            await asyncio.sleep(0.1)

    observer = asyncio.create_task(observe_resources()) if stage_size else None
    try:
        await uvicorn.Server(uvicorn.Config(observe, log_level="warning", loop="asyncio")).serve(sockets=[listener])
    finally:
        if observer:
            observer.cancel()
        writer_release.set()
        listener.close()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4]),
                    listen_host=os.environ.get('EIDOLON_TEST_HOST', '127.0.0.1'),
                    listen_port=int(os.environ.get('EIDOLON_TEST_PORT', '0')),
                    stage_size=os.environ.get('EIDOLON_STAGE_SIZE')))
