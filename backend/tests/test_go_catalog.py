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

from tests.conftest import ClientFactory
from tests.fakes import FakeLLM


async def test_go_catalog_with_real_sc_backend(make_client: ClientFactory):
    binary = os.environ.get("EIDOLON_CORE_BINARY")
    if not binary:
        pytest.skip("set EIDOLON_CORE_BINARY to run Go/FastAPI integration")
    original = make_client(FakeLLM())
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
            cookies={cookie.name: cookie.value for cookie in original.cookies.jar},
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
            direct_history = await original.get("/api/conversations?character_id=mano")
            proxy_history = await gateway.get("/api/conversations?character_id=mano")
            assert proxy_history.status_code == 200
            assert proxy_history.json() == direct_history.json()
            # Auth stays mandatory, even through the Go facade.
            gateway.cookies.clear()
            assert (await gateway.get("/api/conversations?character_id=mano")).status_code == 401
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
