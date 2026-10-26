"""Test-only Uvicorn process: real app/SQLite, fake external providers, cancellation evidence."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time
import sqlite3

import uvicorn

from eidolon.app import create_app
from eidolon.config import LLMSettings, Settings
from eidolon.work_schema import migrate
from eidolon.work_conversations import WorkConversations
from tests.test_work_schema import legacy
from tests.conftest import github_identity
from tests.fakes import FakeCatalog, FakeIdentityProvider, FakeLLM, FakeLLMService, FakeSpeech, build_faked


async def run(directory: Path, snapshot: Path, display: Path, assets_url: str | None) -> None:
    root = Path(__file__).resolve().parents[2]

    class ObservedLLM(FakeLLM):
        async def stream_with(self, key, messages, tools=(), *, forbid_calls=False):
            if any(m.role == "user" and "cancel-stream" in m.content for m in messages):
                try:
                    yield "<ja>はい。</ja><zh>好的。</zh>"
                    (directory / "model-waiting").write_text("waiting in stream_with")
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    (directory / "cancelled.json").write_text(json.dumps({"at": time.monotonic()}))
                    raise
            else:
                async for chunk in super().stream_with(key, messages, tools, forbid_calls=forbid_calls):
                    yield chunk

    github = FakeIdentityProvider("github")
    code = github.will_identify(github_identity())
    settings = Settings(
        database_path=directory / "test.db", characters_path=root / "characters/characters.toml",
        chat_llm=LLMSettings("http://llm.invalid/v1", "test-only", "test-model"),
        ice_servers=[], secret_key="three-process-test-only", dev_login=False,
        character_snapshot_path=snapshot, catalog_display_path=display,
        stand_assets_url=assets_url,
    )
    legacy(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE conversations SET character_id='mano'")
        db.execute("INSERT INTO sign_in_methods VALUES ('u','github','1001','producer',NULL,'2020-01-01')")
    migration = migrate(settings.database_path, directory / "before.db", ("sc", "demo"))
    backend = build_faked(
        settings, ObservedLLM(*[["<ja>はい。</ja><zh>好的。</zh>"]] * 5),
        speech=FakeSpeech(), identity_providers={"github": github},
        own_llm_service=FakeLLMService(), catalog_transport=FakeCatalog().transport(),
    )
    demo = WorkConversations(backend.database, user_id="u", work_id="demo")
    hidden = await demo.create("mano")
    await demo.append(hidden.id, "user", "other-work-private")
    other_user = await backend.store.create_user("other")
    other = await backend.store.create_conversation(other_user.id, "mano")
    app = create_app(backend)

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
    listener.bind(("127.0.0.1", 0))
    (directory / "backend.json").write_text(json.dumps({
        "port": listener.getsockname()[1], "pid": os.getpid(), "code": code,
        "history": "c", "hidden": hidden.id, "other": other.id, "migration": migration,
    }))
    try:
        await uvicorn.Server(uvicorn.Config(observe, log_level="warning", loop="asyncio")).serve(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4])))
