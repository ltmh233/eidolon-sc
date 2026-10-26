"""Consume the private Go world snapshot; no HTTP route exposes its source data.

Go owns source parsing and normalization. World.from_json only materializes the
existing runtime model so date-sensitive prompts and retrieval keep their semantics.
The old source loader remains an explicit rollback path, never an error fallback.
"""

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from eidolon.characters import Character, CharacterConfigError, characters_from_entries
from eidolon.readings import Reading
from eidolon.gift_catalog import Gift, gifts_from_entries
from eidolon.world import World, WorldError

MAX_SNAPSHOT_BYTES = 32 << 20


def _read(path: Path) -> dict:
    with path.open("rb") as source:
        raw = source.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds limit")
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise ValueError("invalid snapshot version")
    if data["schema_version"] in (2,3):
        payload = data.get("payload")
        if not isinstance(payload, str) or hashlib.sha256(payload.encode()).hexdigest() != data.get("sha256"):
            raise ValueError("snapshot digest mismatch")
        bundle = json.loads(payload)
        if not isinstance(bundle, dict):
            raise ValueError("invalid bundle")
        return {**bundle, "schema_version": data["schema_version"], "sha256": data["sha256"]}
    return data


def _world(data: dict) -> World:
    if data["schema_version"] not in (1, 2, 3) or not isinstance(data.get("worlds"), list):
        raise ValueError("unsupported snapshot schema")
    worlds: dict[str, dict] = {}
    for item in data["worlds"]:
        if not isinstance(item, dict):
            raise ValueError("invalid snapshot world")
        work_id = item.get("work_id")
        if not isinstance(work_id, str) or not work_id or work_id in worlds:
            raise ValueError("invalid or duplicate snapshot work_id")
        if not isinstance(item.get("format"), str) or not isinstance(item.get("data"), dict):
            raise ValueError("invalid snapshot world data")
        worlds[work_id] = item
    sc = worlds.get("sc")
    if sc is None or sc["format"] != "sc-world-v1":
        raise ValueError("snapshot requires sc with format sc-world-v1")
    return World.from_json(sc["data"])


def load_sc_world(path: Path) -> World:
    """Read a complete v1 snapshot and select SC explicitly, never the first world."""
    try:
        return _world(_read(path))
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        # Do not include the private snapshot contents in errors or logs.
        raise WorldError(f"Invalid Go world snapshot {path}: {type(error).__name__}") from None


@dataclass(frozen=True)
class SCContent:
    characters: dict[str, Character]
    world: World
    readings: tuple[Reading, ...]
    gifts: dict[str, Gift] | None = None
    digest: str = ""


def load_sc_content(path: Path, config: Path, world_snapshot: Path | None = None) -> SCContent:
    """One v2 batch; retained resource files must match the export's input manifest.

    Raw configuration files are hashed for coherence, never interpreted in this path.
    Deploy the content directory read-only: the manifest is not a hot-reload protocol
    or a signature against an attacker able to rewrite both payload and digest.
    """
    try:
        bundle = _read(path)
        if bundle["schema_version"] not in (2,3):
            raise ValueError("character snapshots require v2")
        if world_snapshot is not None and _read(world_snapshot).get("sha256") != bundle["sha256"]:
            raise ValueError("mixed character/world snapshots")
        world = _world(bundle)
        sets = bundle["characters"]
        if not isinstance(sets, list):
            raise ValueError("missing characters")
        by_work: dict[str, dict] = {}
        for item in sets:
            work = item["work_id"]
            if not isinstance(work, str) or not work or work in by_work:
                raise ValueError("invalid/duplicate character work")
            by_work[work] = item
        if set(by_work) != {w["work_id"] for w in bundle["worlds"]}:
            raise ValueError("work set mismatch")
        sc = by_work["sc"]
        if sc["format"] != {2: "sc-characters-v1", 3: "sc-characters-v2"}[bundle["schema_version"]]:
            raise ValueError("invalid character format")
        data = sc["data"]
        base = config.parent.resolve()

        def safe_path(name: str) -> Path:
            if not isinstance(name, str) or not name or "\\" in name or any(
                not part or part.startswith(".") for part in name.split("/")
            ):
                raise ValueError("invalid content reference")
            candidate = base / name
            if not candidate.resolve().is_relative_to(base):
                raise ValueError("content reference escapes root")
            return candidate

        if data["config"] != config.name:
            raise ValueError("different character configuration")
        safe_path(config.name)
        entries, documents, manifest = data["entries"], data["documents"], data["manifest"]
        if not isinstance(entries, list) or not entries or not isinstance(documents, dict) or not isinstance(manifest, dict):
            raise ValueError("invalid character data")
        required = {config.name, "world.json", "gifts.toml", "readings.toml"}
        for entry in entries:
            for key in ("sheet", "facts", "avatar", "background", "scsp_avatar"):
                if key in entry:
                    safe_path(entry[key])
                    required.add(entry[key])
                    if key in ("sheet", "facts") and entry[key] not in documents:
                        raise ValueError("missing document")
        if not required <= manifest.keys() or not documents.keys() <= manifest.keys():
            raise ValueError("incomplete manifest")
        if any(manifest[name] is None for name in (config.name, "world.json", "gifts.toml")):
            raise ValueError("missing required source")
        total = 0
        for name, expected in manifest.items():
            target = safe_path(name)
            try:
                with target.open("rb") as source:
                    raw = source.read((16 << 20) + 1)
            except FileNotFoundError:
                if expected is not None:
                    raise ValueError("missing content") from None
                raw = None
            if raw is not None:
                total += len(raw)
                if len(raw) > 16 << 20 or total > 64 << 20:
                    raise ValueError("content exceeds limit")
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError("content manifest mismatch")
            if name in documents:
                document = documents[name]
                if document is None:
                    if raw is not None:
                        raise ValueError("document presence mismatch")
                elif not isinstance(document, str) or raw is None or document.encode() != raw:
                    raise ValueError("document mismatch")

        def read_text(target: Path) -> str:
            value = documents[target.relative_to(base).as_posix()]
            if value is None:
                raise FileNotFoundError(target)
            # Match Path.read_text's universal newline handling on the old path.
            return value.replace("\r\n", "\n").replace("\r", "\n")

        readings = tuple(Reading(**r) for r in data["readings"])
        if any(not isinstance(v, str) for r in readings for v in (r.written, r.kana, r.phonemes)):
            raise ValueError("invalid reading")
        characters = characters_from_entries(entries, base / config.name, readings, read_text=read_text)
        world.check_characters(characters, path)
        gifts = gifts_from_entries(data["gifts"]) if bundle["schema_version"] == 3 else None
        return SCContent(characters, world, readings, gifts, bundle["sha256"])
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        # Internal text must not leak through parser diagnostics.
        raise CharacterConfigError(f"Invalid Go character snapshot {path}") from None
