"""Character contract against real Go, with no private texts in assertion diagnostics."""

import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import fields, replace
from pathlib import Path

import pytest
import httpx

import eidolon.assembly
import eidolon.characters
from eidolon.characters import Character, CharacterConfigError, load_characters
from eidolon.config import Settings
from eidolon.content_snapshot import load_sc_content, load_sc_world
from eidolon.readings import load_readings
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import FakeClock, FakeLLM, build_faked, system_of
from tests.test_text_turn import send_turn, start_conversation

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "characters/characters.toml"


def export(config: Path, dest: Path, *, with_gifts: bool = False) -> subprocess.CompletedProcess:
    binary = os.environ.get("EIDOLON_CORE_BINARY")
    if not binary:
        pytest.skip("set EIDOLON_CORE_BINARY for real Go character contracts")
    return subprocess.run([
        binary, "-packs", f"{ROOT}/core/packs,{ROOT}/core/testdata/packs",
        "-sc-world", str(config.parent / "world.json"), "-sc-characters", str(config),
        "-export-worlds", str(dest),
        *(["-with-gifts"] if with_gifts else []),
    ], timeout=15, capture_output=True)


@pytest.fixture
def character_snapshot(tmp_path: Path) -> Path:
    dest = tmp_path / "content.json"
    result = export(CONFIG, dest)
    assert result.returncode == 0, result.stderr.decode()
    return dest


def rewrite(path: Path, bundle: dict) -> None:
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    path.write_text(json.dumps({
        "schema_version": 2, "payload": payload,
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }))


def test_all_character_fields_match_and_source_readers_are_unused(
    character_snapshot: Path, monkeypatch: pytest.MonkeyPatch,
):
    old = load_characters(CONFIG)
    readings = load_readings(CONFIG.parent / "readings.toml")

    def forbidden(*args, **kwargs):
        raise AssertionError("raw TOML parser called in snapshot mode")

    monkeypatch.setattr(eidolon.characters.tomllib, "loads", forbidden)
    content = load_sc_content(character_snapshot, CONFIG)
    assert list(content.characters) == list(old)
    for id, character in old.items():
        for field in fields(Character):
            same = getattr(content.characters[id], field.name) == getattr(character, field.name)
            assert same, (id, field.name)
    same_readings = content.readings == readings
    assert same_readings
    assert content.world == load_sc_world(character_snapshot)
    print(f"full character contract: {len(old)} characters, {len(fields(Character))} fields each")


@pytest.mark.parametrize("assets_url", [None, "https://assets.example.invalid"])
@pytest.mark.parametrize("with_gifts", [False, True])
async def test_catalog_prompts_resources_and_existing_history(
    character_snapshot: Path, settings: Settings, make_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, assets_url: str | None, with_gifts: bool,
):
    if with_gifts:
        assert export(CONFIG, character_snapshot, with_gifts=True).returncode == 0
    old_settings = replace(settings, characters_path=CONFIG, stand_assets_url=assets_url)
    characters = load_characters(CONFIG)
    reply = ["<ja>はい。</ja><zh>好的。</zh>"]
    old_llm = FakeLLM(*[reply for _ in characters])
    new_llm = FakeLLM(*[reply for _ in characters])
    clock = FakeClock()
    old = make_client(old_llm, settings=old_settings, clock=clock)
    conversations = {}
    for id in characters:
        conversation = await start_conversation(old, id)
        await send_turn(old, conversation, "こんにちは")
        conversations[id] = conversation

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy loader called in character snapshot mode")

    for name in ("load_characters", "load_world", "load_readings"):
        monkeypatch.setattr(eidolon.assembly, name, forbidden)
    migrated = make_client(new_llm, clock=clock, settings=replace(
        old_settings, database_path=tmp_path / "fresh.db", character_snapshot_path=character_snapshot,
    ))
    expected = await old.get("/api/characters")
    actual = await migrated.get("/api/characters")
    assert actual.status_code == expected.status_code == 200
    assert actual.json() == expected.json()
    for index, (id, character) in enumerate(characters.items()):
        conversation = await start_conversation(migrated, id)
        await send_turn(migrated, conversation, "こんにちは")
        equal = system_of(new_llm.prompts[index]) == system_of(old_llm.prompts[index])
        assert equal, (id, "system prompt")
        for suffix in ("avatar", "scsp-avatar", "background"):
            before = await old.get(f"/api/characters/{id}/{suffix}")
            after = await migrated.get(f"/api/characters/{id}/{suffix}")
            assert after.status_code == before.status_code, (id, suffix)
            assert hashlib.sha256(after.content).digest() == hashlib.sha256(before.content).digest(), (id, suffix)

    reopened = make_client(FakeLLM(reply), clock=clock, settings=replace(
        old_settings, character_snapshot_path=character_snapshot,
    ))
    for id, conversation in conversations.items():
        route = f"/api/conversations/{conversation}/messages"
        assert (await reopened.get(route)).json() == (await old.get(route)).json(), id
    # Continue an old conversation using the same bare character identity and database.
    events = await send_turn(reopened, next(iter(conversations.values())), "またね")
    assert events[-1]["message"]["ja"] == "はい。"


def test_broken_or_mixed_snapshots_fail_before_database(
    character_snapshot: Path, tmp_path: Path, settings: Settings,
):
    original = character_snapshot.read_bytes()
    original_bundle = json.loads(json.loads(original)["payload"])
    bad = tmp_path / "bad.json"
    database = tmp_path / "must-not-exist.db"

    def rejected(label: str, world: Path | None = None):
        with pytest.raises(CharacterConfigError, match="Invalid Go character snapshot"):
            build_faked(replace(
                settings, characters_path=CONFIG, database_path=database,
                character_snapshot_path=bad, world_snapshot_path=world,
            ))
        assert not database.exists(), label

    rejected("missing")
    for label, raw in (("truncated", b'{'), ("version", b'{"schema_version":1}'),
                       ("digest", original.replace(b'"sha256":"', b'"sha256":"0', 1))):
        bad.write_bytes(raw)
        rejected(label)
    for label in ("work", "duplicate", "path", "manifest", "missing_document", "mixed_world"):
        bundle = json.loads(json.dumps(original_bundle))
        sc = next(c for c in bundle["characters"] if c["work_id"] == "sc")
        data = sc["data"]
        if label == "work":
            sc["work_id"] = "other"
        elif label == "duplicate":
            data["entries"].append(data["entries"][0])
        elif label == "path":
            data["entries"][0]["sheet"] = "../private.txt"
        elif label == "manifest":
            data["manifest"]["characters.toml"] = "0" * 64
        elif label == "missing_document":
            del data["documents"][data["entries"][0]["sheet"]]
        else:
            bundle["worlds"][0]["data"]["setting"] += "different"
        rewrite(bad, bundle)
        rejected(label, character_snapshot if label == "mixed_world" else None)


def test_source_changes_missing_assets_and_failed_publication(
    characters: CharacterConfig, tmp_path: Path,
):
    """Only synthetic files are edited; publication failures retain the valid snapshot."""
    characters.write("world.json", json.dumps({
        "as_of": "2026-10-25", "office": {"name": "283", "staff": []}, "units": [],
        "idols": [{"name": name, "reading": "test", "unit": None, "cv": None, "sources": []}
                  for name in ("櫻木真乃", "月岡恋鐘")],
    }))
    characters.write("sheets/mano.md", "# sheet\r\nHello\r\nWorld\r\n")
    config = characters.path.read_text().replace("avatars/mano.png", "missing.png")
    characters.path.write_text(config)
    dest = tmp_path / "bundle.json"
    assert export(characters.path, dest).returncode == 0
    old = load_characters(characters.path)
    new = load_sc_content(dest, characters.path).characters
    assert new["mano"].avatar is None
    for id in old:
        same = old[id] == new[id]
        assert same, id
    before = dest.read_bytes()
    characters.write("sheets/mano.md", "different version")
    with pytest.raises(CharacterConfigError):
        load_sc_content(dest, characters.path)
    for label, bad_config in (
        ("malformed", "[[characters"),
        ("outside", config.replace("sheets/mano.md", "../private.txt")),
        ("duplicate", config + config),
    ):
        characters.path.write_text(bad_config)
        assert export(characters.path, dest).returncode != 0, label
        assert dest.read_bytes() == before, label
    characters.path.write_text(config)
    # An optional asset appearing after export is also a version mismatch.
    assert export(characters.path, dest).returncode == 0
    characters.write("missing.png", b"new asset")
    with pytest.raises(CharacterConfigError):
        load_sc_content(dest, characters.path)
    assert export(characters.path, dest).returncode == 0
    before = dest.read_bytes()
    asset = characters.directory / "missing.png"
    asset.unlink()  # synthetic optional image, now recorded as present in the manifest
    with pytest.raises(CharacterConfigError):
        load_sc_content(dest, characters.path)
    asset.write_bytes(b"new asset")
    outside = tmp_path / "outside.md"
    sheet = characters.directory / "sheets/mano.md"
    outside.write_bytes(sheet.read_bytes())
    sheet.unlink()  # generated fixture only; never a repository file
    sheet.symlink_to(outside)
    with pytest.raises(CharacterConfigError):
        load_sc_content(dest, characters.path)
    assert export(characters.path, dest).returncode != 0
    assert dest.read_bytes() == before


async def test_native_static_catalog_without_python(character_snapshot: Path):
    binary = os.environ["EIDOLON_CORE_BINARY"]
    process = await asyncio.create_subprocess_exec(
        binary, "-listen", "127.0.0.1:0", "-legacy", "http://127.0.0.1:1",
        "-packs", f"{ROOT}/core/packs,{ROOT}/core/testdata/packs",
        "-content-snapshot", str(character_snapshot), stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stderr is not None
        ready = await asyncio.wait_for(process.stderr.readline(), 10)
        assert b"catalog listening on " in ready, ready
        address = ready.decode().strip().split("catalog listening on ", 1)[1]
        async with httpx.AsyncClient(base_url=f"http://{address}", trust_env=False) as client:
            response = await client.get("/api/works/sc/static-characters")
            assert response.status_code == 200
            rows = response.json()["characters"]
            old = load_characters(CONFIG)
            assert [r["local_id"] for r in rows] == list(old)
            for row in rows:
                c = old[row["local_id"]]
                assert row["id"] == f"sc:{c.id}"
                assert row["display"] == {
                    "id": c.id, "name": c.name, "intro": c.intro, "color": c.color,
                    "birthday": f"{c.birthday.month:02}-{c.birthday.day:02}",
                    **{key: getattr(c, key) for key in ("name_zh", "name_kana", "name_romaji")
                       if getattr(c, key) is not None},
                }
            demo = (await client.get("/api/works/demo/static-characters")).json()["characters"]
            assert demo[0]["local_id"] == "mano"
            assert demo[0]["id"] == "demo:mano" != rows[0]["id"]
            for route, code in (
                ("/api/works/unknown/static-characters", 404),
                ("/api/works/sc/static-characters/missing", 404),
                ("/api/works/sc/static-characters?work_id=demo", 400),
                ("/api/works/sc/characters", 502),  # full display still needs Python
            ):
                assert (await client.get(route)).status_code == code, route
            assert (await client.post("/api/works/demo/conversations")).status_code == 405
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                process.kill()
                await process.wait()
