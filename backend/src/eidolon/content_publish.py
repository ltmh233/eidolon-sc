"""Offline display publication; no model, database, credentials or HTTP service needed.

Run python -m eidolon.content_publish --help inside the prepared content build environment.
"""

import argparse
import json
import os
from pathlib import Path
import tempfile

from eidolon.character_catalog import display_characters
from eidolon.content_snapshot import load_sc_content
from eidolon.content_snapshot import SCContent


def verify_display(path: Path, content: SCContent, assets_url: str | None) -> None:
    """Fail startup if Go's published catalog and Python's deployment disagree."""
    with path.open("rb") as source:
        raw = source.read((2 << 20) + 1)
    if len(raw) > 2 << 20:
        raise ValueError("catalog metadata exceeds limit")
    actual = json.loads(raw)
    if not isinstance(actual, dict) or type(actual.get("schema_version")) is not int:
        raise ValueError("invalid catalog metadata schema")
    expected = {
        "schema_version": 1, "content_sha256": content.digest, "assets_url": assets_url,
        "characters": display_characters(content.characters.values(), content.world, assets_url),
    }
    if actual != expected:
        raise ValueError("catalog/content/deployment mismatch")


def publish(snapshot: Path, config: Path, assets_url: str | None, output: Path) -> None:
    content = load_sc_content(snapshot, config)
    if assets_url is not None:
        assets_url = assets_url.strip().rstrip("/") or None
        if assets_url and not assets_url.startswith(("http://", "https://")):
            raise ValueError("assets URL must use http(s)")
    data = {
        "schema_version": 1, "content_sha256": content.digest, "assets_url": assets_url,
        "characters": display_characters(content.characters.values(), content.world, assets_url),
    }
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".display-", delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(json.dumps(data, ensure_ascii=False).encode())
            f.flush()
            os.fsync(f.fileno())
            f.close()
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--characters", type=Path, required=True)
    parser.add_argument("--assets-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    publish(args.snapshot, args.characters, args.assets_url, args.output)


if __name__ == "__main__":
    main()
