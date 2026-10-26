"""v3 gifts, offline public projection, mixed-release rejection and v2 rollback."""
import json
import os
import subprocess
from dataclasses import fields, replace
from pathlib import Path

import pytest

import eidolon.assembly
import eidolon.content_publish
from eidolon.characters import Character, CharacterConfigError, load_characters
from eidolon.content_snapshot import load_sc_content
from eidolon.content_publish import publish
from eidolon.gift_catalog import load_gift_catalog
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM, build_faked
from tests.test_character_snapshot import CONFIG, ROOT, export


async def test_unified_release_and_publication_contract(
    tmp_path: Path, settings: Settings, make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch,
):
    snapshot, display = tmp_path / "v3.json", tmp_path / "display.json"
    assert export(CONFIG, snapshot, with_gifts=True).returncode == 0
    original_gifts = load_gift_catalog(CONFIG.parent / "gifts.toml")
    original_characters = load_characters(CONFIG)
    content = load_sc_content(snapshot, CONFIG)
    assert content.gifts is not None
    assert content.gifts == original_gifts
    assert list(content.gifts) == list(original_gifts)
    for id, character in original_characters.items():
        for field in fields(Character):
            equal = getattr(character, field.name) == getattr(content.characters[id], field.name)
            assert equal, (id, field.name)
    old = make_client(FakeLLM(), settings=replace(settings, characters_path=CONFIG))
    expected = (await old.get("/api/characters")).json()
    publish(snapshot, CONFIG, None, display)
    assert json.loads(display.read_text())["characters"] == expected

    def forbidden(*args, **kwargs):
        raise AssertionError("v3 runtime called raw static loader")

    for field in ("load_characters", "load_world", "load_readings", "load_gift_catalog"):
        monkeypatch.setattr(eidolon.assembly, field, forbidden)
    configured = replace(settings, characters_path=CONFIG, character_snapshot_path=snapshot,
                         catalog_display_path=display, database_path=tmp_path / "new.db")
    new = make_client(FakeLLM(), settings=configured)
    assert (await new.get("/api/characters")).json() == expected
    print(f"RELEASE contract: {len(original_characters)} characters; {len(original_gifts)} gifts; all raw runtime loaders disabled")
    # Changing deployment assets without regenerating metadata cannot start the consumer.
    with pytest.raises(ValueError, match="mismatch"):
        build_faked(replace(configured, stand_assets_url="https://other.invalid", database_path=tmp_path / "blocked.db"))
    assert not (tmp_path / "blocked.db").exists()
    old_bytes = display.read_bytes()
    with pytest.raises(CharacterConfigError):
        publish(tmp_path / "absent.json", CONFIG, None, display)
    assert display.read_bytes() == old_bytes
    with monkeypatch.context() as interrupted:
        def stop_before_replace(*args):
            raise KeyboardInterrupt("simulated publication interruption before atomic replace")
        interrupted.setattr(eidolon.content_publish.os, "replace", stop_before_replace)
        with pytest.raises(KeyboardInterrupt):
            publish(snapshot, CONFIG, None, display)
    assert display.read_bytes() == old_bytes
    # Existing v2 CLI still exports v2 and can be used as an explicit rollback.
    rollback = tmp_path / "v2.json"
    assert export(CONFIG, rollback).returncode == 0
    assert json.loads(rollback.read_text())["schema_version"] == 2
    assert load_sc_content(rollback, CONFIG).gifts is None
    with pytest.raises(ValueError, match="mismatch"):
        build_faked(replace(configured, character_snapshot_path=rollback, database_path=tmp_path / "mixed.db"))
    assert not (tmp_path / "mixed.db").exists()
    # Go rejects a different content release and unknown public fields (including nested ones).
    binary = os.environ["EIDOLON_CORE_BINARY"]
    for label in ("digest", "private", "nested"):
        metadata = json.loads(old_bytes)
        if label == "digest":
            metadata["content_sha256"] = "0" * 64
        elif label == "private":
            metadata["characters"][0]["sheet"] = "must not publish"
        else:
            metadata["characters"][0]["greeting"]["voice_id"] = "must not publish"
        invalid = tmp_path / f"{label}.json"
        invalid.write_text(json.dumps(metadata))
        result = subprocess.run([
            binary, "-packs", f"{ROOT}/core/packs,{ROOT}/core/testdata/packs",
            "-content-snapshot", str(snapshot), "-catalog-display", str(invalid),
        ], timeout=5, capture_output=True)
        assert result.returncode != 0, label
