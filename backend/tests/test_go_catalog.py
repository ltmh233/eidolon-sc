"""Opt-in real Go -> FastAPI HTTP contract test; CI supplies the Go executable.

External LLM/OAuth providers use existing fakes. API handlers, SQLite, HTTP and Go
are real. No production configuration or database is used.
"""

import asyncio
import os
from pathlib import Path
import socket
from typing import cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from tests.conftest import ClientFactory, agree_to_terms, github_identity, sign_in_with
from tests.fakes import FakeIdentityProvider, FakeLLM
from tests.test_text_turn import send_turn


async def test_go_catalog_with_real_sc_backend(
    make_client: ClientFactory, github: FakeIdentityProvider,
):
    binary = os.environ.get("EIDOLON_CORE_BINARY")
    if not binary:
        pytest.skip("set EIDOLON_CORE_BINARY to run Go/FastAPI integration")
    llm = FakeLLM(*[["<ja>はいっ</ja><zh>好的</zh>"]] * 2)
    original = make_client(llm)
    transport = cast(httpx.ASGITransport, original._transport)
    server = uvicorn.Server(uvicorn.Config(
        cast(FastAPI, transport.app), lifespan="off", log_level="error", loop="asyncio",
    ))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    root = Path(__file__).resolve().parents[2]
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("Python server stopped before startup")
                await asyncio.sleep(0.01)
        process = await asyncio.create_subprocess_exec(
            binary, "-listen", "127.0.0.1:0", "-legacy", f"http://127.0.0.1:{port}",
            "-packs", f"{root}/core/packs,{root}/core/testdata/packs",
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        ready = await asyncio.wait_for(process.stderr.readline(), timeout=10)
        assert b"catalog listening on " in ready, ready
        address = ready.decode().strip().split("catalog listening on ", 1)[1]
        async with httpx.AsyncClient(
            base_url=f"http://{address}",
            cookies={
                cookie.name: cookie.value for cookie in original.cookies.jar
                if cookie.value is not None
            },
            headers={"Origin": "http://localhost"}, trust_env=False,
        ) as gateway:
            direct = await original.get("/api/characters")
            proxied = await gateway.get("/api/characters")
            assert proxied.status_code == direct.status_code == 200
            assert proxied.json() == direct.json()
            scoped = (await gateway.get("/api/works/sc/characters")).json()
            assert [c["display"] for c in scoped["characters"]] == direct.json()
            assert scoped["characters"][0]["id"] == "sc:mano"
            demo = (await gateway.get("/api/works/demo/characters")).json()
            assert demo["characters"][0]["id"] == "demo:mano"
            assert "星灯学院" in (await gateway.get("/api/works/demo/world")).text
            assert (await gateway.get("/api/works/demo/rag")).status_code == 501
            assert (await gateway.post("/api/conversations", json={
                "character_id": "mano", "work_id": "demo",
            })).status_code == 409
            # Existing SC data created through Python is visible through Go, unchanged.
            created = await original.post("/api/conversations", json={"character_id": "mano"})
            assert created.status_code == 201
            conversation_id = created.json()["id"]
            history_path = f"/api/conversations/{conversation_id}/messages"
            before = (await original.get(history_path)).json()
            # A body-only scope must not silently run a DLC turn in this SC conversation.
            for entry, client in (("gateway", gateway), ("direct", original)):
                for scope in ("demo", None):
                    case = f"{entry}, scope={scope!r}"
                    rejected = await client.post(
                        f"/api/conversations/{conversation_id}/turns",
                        json={"text": "不可写入 SC", "work_id": scope},
                    )
                    assert rejected.status_code == 409, f"{case}: {rejected.text}"
                    assert (await original.get(history_path)).json() == before, case
                    assert not llm.prompts, case
            events = await send_turn(gateway, conversation_id, "你好")
            assert events[0] == {"type": "user", "text": "你好"}
            assert events[-1]["type"] == "done"
            assert events[-1]["message"]["zh"] == "好的"
            direct_messages = await original.get(history_path)
            proxy_messages = await gateway.get(history_path)
            assert proxy_messages.status_code == direct_messages.status_code == 200
            assert proxy_messages.json() == direct_messages.json()
            direct_history = await original.get("/api/conversations?character_id=mano")
            proxy_history = await gateway.get("/api/conversations?character_id=mano")
            assert proxy_history.status_code == 200
            assert proxy_history.json() == direct_history.json()
            explicit_sc = await gateway.post(
                f"/api/conversations/{conversation_id}/turns",
                json={"text": "继续", "work_id": "sc"},
            )
            assert explicit_sc.status_code == 200
            assert '"type": "done"' in explicit_sc.text
            assert (await gateway.post(
                "/api/conversations", json={"character_id": "mano"},
                headers={"Origin": "https://attacker.invalid"},
            )).status_code == 403
            no_origin = gateway.build_request(
                "POST", "/api/conversations", json={"character_id": "mano"},
            )
            del no_origin.headers["Origin"]
            assert (await gateway.send(no_origin)).status_code == 403
            # Auth stays mandatory, even through the Go facade.
            gateway.cookies.clear()
            assert (await gateway.get("/api/conversations?character_id=mano")).status_code == 401
            rejected = await gateway.get(
                "/api/auth/github/callback", params={"code": "fake", "state": "wrong"},
            )
            assert rejected.status_code == 302
            assert rejected.headers["location"] == "/login?error=state"
            signed_in = await sign_in_with(gateway, github, github_identity())
            assert signed_in.status_code == 302
            assert signed_in.headers["location"] == "/conversations"
            cookies = signed_in.headers.get_list("set-cookie")
            assert any("eidolon_session=" in c and "HttpOnly" in c and "SameSite=lax" in c for c in cookies)
            assert (await gateway.get("/api/conversations?character_id=mano")).status_code == 428
            await agree_to_terms(gateway)
            assert (await gateway.get("/api/conversations?character_id=mano")).json() == []
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()
