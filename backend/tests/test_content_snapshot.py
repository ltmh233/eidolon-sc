"""Real Go export compared with the entire reviewed SC world, not a Mano fixture."""

import json
import os
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import eidolon.assembly
from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon.content_snapshot import load_sc_world
from eidolon.world import WorldError, load_world
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM, build_faked, system_of
from tests.test_text_turn import send_turn, start_conversation

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    binary = os.environ.get("EIDOLON_CORE_BINARY")
    if not binary:
        pytest.skip("set EIDOLON_CORE_BINARY for the real Go content contract")
    dest = tmp_path / "worlds.json"
    subprocess.run([
        binary, "-packs", f"{ROOT}/core/packs,{ROOT}/core/testdata/packs",
        "-sc-world", str(ROOT / "characters/world.json"), "-export-worlds", str(dest),
    ], check=True, timeout=10, capture_output=True)
    return dest


def test_all_sc_world_content_and_date_boundaries(snapshot: Path):
    old = load_world(ROOT / "characters/world.json")
    new = load_sc_world(snapshot)
    assert old is not None
    assert new == old  # every nested field, ordering, source, CV and null
    assert new.entries() == old.entries()  # the complete RAG input, including setlists
    characters = load_characters(ROOT / "characters/characters.toml")
    dates = {old.as_of} | {r.date for r in old.releases} | {
        day.date for live in old.lives for day in live.days
    }
    boundaries = {day + timedelta(days=offset) for day in dates for offset in (-1, 0, 1)}
    for character in characters.values():
        name = character.name
        assert new.unit_of(name) == old.unit_of(name), character.id
        assert new.songs_of(name) == old.songs_of(name), character.id
        assert new.lives_of(name) == old.lives_of(name), character.id
        for day in boundaries:
            assert new.prompt(name, day) == old.prompt(name, day), (character.id, day)
    envelope = json.loads(snapshot.read_text())
    assert [w["work_id"] for w in envelope["worlds"]] == ["demo", "sc"]
    assert envelope["worlds"][0]["format"] == "text-world-v1"
    assert envelope["worlds"][0]["data"] != envelope["worlds"][1]["data"]
    print(f"SC contract: {len(characters)} characters, {len(boundaries)} date boundaries, "
          f"{len(old.entries())} retrieval entries")


async def test_real_catalog_uses_snapshot_without_raw_world_fallback(
    snapshot: Path, settings: Settings, make_client: ClientFactory,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    original_settings = replace(settings, characters_path=ROOT / "characters/characters.toml")
    characters = load_characters(original_settings.characters_path)
    reply = ["<ja>はい。</ja><zh>好的。</zh>"]
    old_llm = FakeLLM(*[reply for _ in characters])
    new_llm = FakeLLM(*[reply for _ in characters])
    original = make_client(old_llm, settings=original_settings)
    expected = await original.get("/api/characters")
    assert expected.status_code == 200

    def forbidden_raw_read(path: Path):
        raise AssertionError(f"snapshot mode read raw world: {path}")

    monkeypatch.setattr(eidolon.assembly, "load_world", forbidden_raw_read)
    migrated = make_client(new_llm, settings=replace(
        original_settings, database_path=tmp_path / "migrated.db", world_snapshot_path=snapshot,
    ))
    actual = await migrated.get("/api/characters")
    assert actual.status_code == 200
    assert actual.json() == expected.json()  # all characters and all public fields/order
    for character_id in characters:
        old_id = await start_conversation(original, character_id)
        new_id = await start_conversation(migrated, character_id)
        await send_turn(original, old_id, "こんにちは")
        await send_turn(migrated, new_id, "こんにちは")
        assert system_of(new_llm.prompts[-1]) == system_of(old_llm.prompts[-1]), character_id
    # Reopen the original database with the new loader. Existing IDs/history still resolve.
    reopened = make_client(FakeLLM(), settings=replace(
        original_settings, world_snapshot_path=snapshot,
    ))
    for character_id in characters:
        params = {"character_id": character_id}
        assert (await reopened.get("/api/conversations", params=params)).json() == (
            await original.get("/api/conversations", params=params)
        ).json(), character_id


@pytest.mark.parametrize("payload", [
    {}, {"schema_version": True, "worlds": []}, {"schema_version": 2, "worlds": []},
    {"schema_version": 1, "worlds": []},
    {"schema_version": 1, "worlds": [{"work_id": "demo", "format": "sc-world-v1", "data": {}}]},
    {"schema_version": 1, "worlds": [{"work_id": "sc", "format": "unknown", "data": {}}]},
    {"schema_version": 1, "worlds": [{"work_id": "sc", "format": "sc-world-v1", "data": {}}]},
    {"schema_version": 1, "worlds": [
        {"work_id": "sc", "format": "sc-world-v1", "data": {}},
        {"work_id": "sc", "format": "sc-world-v1", "data": {}},
    ]},
])
def test_invalid_snapshot_fails_before_database_creation(
    payload: dict, tmp_path: Path, settings: Settings,
):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload))
    database = tmp_path / "must-not-exist.db"
    with pytest.raises(WorldError, match="Invalid Go world snapshot"):
        build_faked(replace(settings, world_snapshot_path=path, database_path=database))
    assert not database.exists()


def test_missing_and_truncated_snapshots_fail_closed(tmp_path: Path):
    path = tmp_path / "missing.json"
    with pytest.raises(WorldError):
        load_sc_world(path)
    path.write_bytes(b'{"schema_version":1,')
    with pytest.raises(WorldError):
        load_sc_world(path)
