"""Opt-in real Next production -> Go -> Uvicorn/FastAPI -> temporary SQLite contract."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time
import tempfile
import shutil
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from eidolon.content_publish import publish
from tests.test_character_snapshot import CONFIG, ROOT, export


async def ready(url: str) -> None:
    async with httpx.AsyncClient(trust_env=False) as client:
        async with asyncio.timeout(15):
            while True:
                try:
                    if (await client.get(url)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.parametrize("assets_url", [None, "https://assets.example.invalid"])
async def test_real_three_process_chain(tmp_path: Path, assets_url: str | None):
    with tempfile.TemporaryDirectory(dir=tmp_path) as temporary:
        try:
            await run_chain(Path(temporary), assets_url)
        except BaseException:
            for log in Path(temporary).glob("*.log"):
                shutil.copyfile(log, tmp_path / log.name)
            raise


async def run_chain(tmp_path: Path, assets_url: str | None):
    next_server = os.environ.get("EIDOLON_NEXT_SERVER")
    binary = os.environ.get("EIDOLON_CORE_BINARY")
    if not next_server or not binary:
        pytest.skip("set EIDOLON_NEXT_SERVER and EIDOLON_CORE_BINARY for three-process production acceptance")
    snapshot, display = tmp_path / "content.json", tmp_path / "display.json"
    import secrets
    token = tmp_path / "core-token"
    token.write_text(secrets.token_hex(32))
    token.chmod(0o600)
    result = export(CONFIG, snapshot, with_gifts=True)
    assert result.returncode == 0, result.stderr.decode()
    publish(snapshot, CONFIG, assets_url, display)
    processes = []
    logs = []

    async def spawn(name, *command, env=None):
        log = (tmp_path / f"{name}.log").open("wb")
        logs.append(log)
        process = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log, env=env)
        processes.append(process)
        return process

    try:
        backend = await spawn("python", sys.executable, "-m", "tests.three_process_backend",
                              str(tmp_path), str(snapshot), str(display), json.dumps(assets_url))
        async with asyncio.timeout(15):
            while not (tmp_path / "backend.json").exists():
                assert backend.returncode is None, (tmp_path / "python.log").read_text()
                await asyncio.sleep(0.05)
        info = json.loads((tmp_path / "backend.json").read_text())
        python_url = f"http://127.0.0.1:{info['port']}"
        await ready(python_url + "/api/characters")
        go_port, next_port = port(), port()
        go_url, next_url = f"http://127.0.0.1:{go_port}", f"http://127.0.0.1:{next_port}"
        core = await spawn("go", binary, "-listen", f"127.0.0.1:{go_port}", "-legacy", python_url,
                           "-packs", f"{ROOT}/core/packs,{ROOT}/core/testdata/packs",
                           "-content-snapshot", str(snapshot), "-catalog-display", str(display),
                           "-affinity-token-file", str(token), "-stats-token-file", str(token))
        await ready(go_url + "/api/works")
        next_process = await spawn("next", "node", next_server, env={
            **os.environ, "PORT": str(next_port), "HOSTNAME": "127.0.0.1", "BACKEND_URL": go_url,
        })
        await ready(next_url + "/api/works")
        assert len({backend.pid, core.pid, next_process.pid, os.getpid()}) == 4
        assert info["pid"] == backend.pid
        print(f"TOPOLOGY Next pid={next_process.pid} -> Go pid={core.pid} -> Uvicorn pid={backend.pid}; SQLite temporary")
        async with httpx.AsyncClient(base_url=next_url, headers={"Origin": "http://localhost"}, trust_env=False) as client:
            direct = await client.get(python_url + "/api/characters")
            proxied = await client.get("/api/characters")
            native = await client.get("/api/works/sc/characters")
            assert native.status_code == proxied.status_code == direct.status_code == 200
            assert [c["display"] for c in native.json()["characters"]] == proxied.json() == direct.json()
            go_native = await client.get(go_url + "/api/works/sc/characters")
            assert go_native.headers["x-content-id"] == json.loads(display.read_text())["content_sha256"]
            assert native.json() == go_native.json()  # Next deliberately filters non-public response headers.
            assert native.json()["characters"][0]["id"] == "sc:mano"
            assert (await client.get("/api/works/demo/characters")).json()["characters"][0]["id"] == "demo:mano"
            assert (await client.get("/api/works/absent/characters")).status_code == 404
            assert (await client.get("/api/conversations?character_id=mano")).status_code == 401
            start = await client.get("/api/auth/github/start")
            assert start.status_code == 302
            assert urlsplit(start.headers["location"]).hostname == "provider.invalid"
            state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
            callback = await client.get("/api/auth/github/callback", params={"code": info["code"], "state": state})
            assert callback.status_code == 302
            cookies = callback.headers.get_list("set-cookie")
            assert len(cookies) >= 2, cookies
            assert any("eidolon_session=" in cookie and "HttpOnly" in cookie for cookie in cookies)
            assert (await client.post("/api/account/consent", json={"agree_to_terms": True, "at_least_13": True})).status_code == 204
            affinity_path = "/api/characters/mano/affinity"
            computed = await client.get(affinity_path)
            direct_affinity = await client.get(python_url + affinity_path)
            assert computed.status_code == direct_affinity.status_code == 200
            assert computed.json() == direct_affinity.json() == {"tier": "cold", "cooldown_until": None}
            for origin in ("", go_url):
                denied = await client.get(origin + "/api/_core/affinity/mano", headers={"Authorization": "Bearer " + token.read_text()})
                assert denied.status_code == 404 and "value" not in denied.text, origin
            print("PASS Go affinity computation through private authenticated snapshot; public raw routes denied")
            for span in ('all', '30d', '1y'):
                computed_stats = await client.get('/api/stats', params={'range': span}, headers={'X-Timezone': 'America/New_York'})
                direct_stats = await client.get(python_url + '/api/stats', params={'range': span}, headers={'X-Timezone': 'America/New_York'})
                assert computed_stats.status_code == direct_stats.status_code == 200, (span, computed_stats.text)
                assert computed_stats.json() == direct_stats.json(), (span, computed_stats.text, direct_stats.text)
            assert (await client.get('/api/_core/stats')).status_code == 404
            print('PASS Go native statistics through Next with historical SC conversation and memory')
            from tests.test_sc_play_scope import gameplay_snapshot
            before_scope = gameplay_snapshot(tmp_path / "test.db")
            for boundary, origin in (("next-go-python", ""), ("direct", python_url)):
                for path, body in (
                    ("/api/gifts/draws", {"all": False, "work_id": "demo"}),
                    ("/api/characters/mano/gifts", {"gift_id": "cake", "work_id": None}),
                ):
                    rejected = await client.post(origin + path, json=body)
                    assert rejected.status_code == 409, (boundary, path, body, rejected.text)
                    assert gameplay_snapshot(tmp_path / "test.db") == before_scope, (boundary, path, body)
                rejected = await client.get(origin + "/api/gifts?work_id=sc&work_id=demo")
                assert rejected.status_code == 409, (boundary, "GET gifts", rejected.text)
                assert gameplay_snapshot(tmp_path / "test.db") == before_scope, (boundary, "GET gifts")
            print("PASS SC gameplay scope rejection without SQLite effects through Next/Go and direct Python")
            from uuid import uuid4
            operation_id = str(uuid4())
            grant_route = "/api/admin/gifts/reset-card-grants"
            first_grant = await client.post(grant_route, headers={"Idempotency-Key": operation_id})
            assert first_grant.status_code == 200, first_grant.text
            # Treat the first result as lost; a repeat must return the persisted outcome.
            assert (await client.put("/api/admin/config/affinity", json={"enabled": False})).status_code == 200
            assert (await client.get(affinity_path)).json() is None
            before_replay = gameplay_snapshot(tmp_path / "test.db")
            replay = await client.post(grant_route, headers={"Idempotency-Key": operation_id})
            assert replay.status_code == 200 and replay.json() == first_grant.json(), replay.text
            assert gameplay_snapshot(tmp_path / "test.db") == before_replay, "closed replay wrote gameplay state"
            for label, key in (("empty", ""), ("combined", operation_id + "," + str(uuid4()))):
                invalid = await client.post(grant_route, headers={"Idempotency-Key": key})
                assert invalid.status_code == 400, (label, invalid.text)
                assert gameplay_snapshot(tmp_path / "test.db") == before_replay, label
            assert (await client.put("/api/admin/config/affinity", json={"enabled": True})).status_code == 200
            print("PASS durable grant replay and invalid keys through Next/Go/Python")
            previous = await client.get(f"/api/conversations/{info['history']}/messages")
            assert previous.status_code == 200 and "原文" in previous.text
            assert info["migration"]["counts"]["messages"] == 1
            resumed = await client.post(f"/api/conversations/{info['history']}/turns", json={"text": "continue migrated history"})
            assert resumed.status_code == 200 and '"done"' in resumed.text
            memories = await client.get("/api/memories")
            assert memories.status_code == 200 and "制作人喜欢咖啡" in memories.text
            assert "DEMO_SECRET_MEMORY" not in memories.text
            assert (tmp_path / "memory-recalled").exists(), "historical SC memory missing from actual model prompt"
            assert not (tmp_path / "memory-leaked").exists(), "foreign work memory reached model"
            for boundary, hidden_id in (("cross-work", info["hidden"]), ("cross-user", info["other"])):
                assert (await client.get(f"/api/conversations/{hidden_id}/messages")).status_code == 404, (boundary, "GET messages")
                assert (await client.delete(f"/api/conversations/{hidden_id}")).status_code == 404, (boundary, "DELETE")
                assert (await client.post(f"/api/conversations/{hidden_id}/turns", json={"text": "must refuse"})).status_code == 404, (boundary, "POST turns")
            print("PASS migrated SC history read/resume; cross-work/user HTTP read/write/delete rejected")
            created = await client.post("/api/conversations", json={"character_id": "mano"}, headers={"X-Work-ID": "sc"})
            assert created.status_code == 201, created.text
            conversation = created.json()["id"]
            route = f"/api/conversations/{conversation}/turns"
            for body, headers, expected in (
                ({"text": "no", "work_id": "demo"}, {}, 409),
                ({"text": "no"}, {"X-Work-ID": "demo"}, 409),
                ({"text": "no"}, {"Origin": "https://attacker.invalid"}, 403),
            ):
                assert (await client.post(route, json=body, headers=headers)).status_code == expected, (body, headers)
            assert (await client.post("/api/conversations", json={"character_id": "missing"})).status_code == 404
            assert (await client.post("/api/works/demo/conversations", json={})).status_code == 405
            started = time.monotonic()
            events = []
            first = None
            async with client.stream("POST", route, json={"text": "こんにちは"}) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        events.append(event)
                        if first is None and event["type"] == "delta" and event["text"]:
                            first = time.monotonic() - started
            assert first is not None and first < 2
            assert events[-1]["type"] == "done"
            print(f"METRIC three_process first_ms={first*1000:.3f}")
            history = f"/api/conversations/{conversation}/messages"
            assert (await client.get(history)).json() == (await client.get(python_url + history)).json()
            disconnected = None
            async with client.stream("POST", route, json={"text": "cancel-stream"}) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and json.loads(line[6:])["type"] == "delta" and json.loads(line[6:])["text"]:
                        async with asyncio.timeout(2):
                            while not (tmp_path / "model-waiting").exists():
                                await asyncio.sleep(0.01)
                        print("MODEL waiting in stream_with before client disconnect")
                        disconnected = time.monotonic()
                        break
            async with asyncio.timeout(2):
                while not (tmp_path / "cancelled.json").exists():
                    await asyncio.sleep(0.01)
            cancelled = json.loads((tmp_path / "cancelled.json").read_text())["at"]
            assert disconnected is not None
            delay = cancelled - disconnected
            assert 0 <= delay < 2
            print(f"METRIC three_process llm_cancel_ms={delay*1000:.3f}")
            observed = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
            print(f"OBSERVED Uvicorn ASGI spec: {sorted({q['asgi_spec'] for q in observed})}")
            assert any(q["path"] == "/api/conversations" and q["scope"] == "sc" and q["origin"] == "http://localhost" for q in observed)
            assert any(q["path"] == "/api/_core/affinity/mano" for q in observed), "Go did not request raw affinity state"
            assert any(q["path"] == "/api/_core/stats" for q in observed), "Go did not request raw statistics"
            # Stop Python and prove the complete scoped catalog remains native to Go.
            backend.terminate()
            await asyncio.wait_for(backend.wait(), 10)
            offline = await client.get("/api/works/sc/characters")
            assert offline.json() == native.json()
            print("PASS three-process catalog/chat/auth/history/cancellation; complete catalog survives Python stop")
    finally:
        for process in reversed(processes):
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for log in logs:
            log.close()
