"""Public display projection, shared by the legacy API and offline content publication."""
from collections.abc import Iterable

from eidolon.characters import Character, ScriptedLine, in_idol_order
from eidolon.greetings import (
    call_opener_path,
    call_poster_path,
    greeting_path,
    voxels_path,
)
from eidolon.stand_outfits import list_path, outfit_path
from eidolon.world import World


def _stand(character: Character, assets_url: str | None) -> dict | None:
    """Her stand on the call screen: where her default outfit's files are, and her outfit
    list (`stand_outfits`). None, and her call background alone, unless her idol, her
    default outfit and the stand assets are all configured."""
    if character.idol_id is None or character.default_outfit is None or assets_url is None:
        return None
    outfit = character.default_outfit
    return {
        "default_outfit": {"id": outfit, "url": f"{assets_url}/{outfit_path(outfit)}"},
        "outfits_url": f"{assets_url}/{list_path(character.idol_id)}",
    }


def _greeting(character: Character, assets_url: str | None) -> dict | None:
    """Her greeting on the landing page: what she says as people see it, its subtitle, and
    where `eidolon greeting upload` puts its audio (`greeting_path`), without asking the
    bucket whether it is there; no audio unless the stand assets and her voice are
    configured. None without a greeting."""
    return _scripted(character.greeting, greeting_path(character), assets_url)


def _call_opener(character: Character, assets_url: str | None) -> dict | None:
    """Her call opener on the landing page, as her greeting is (`call_opener_path`); None
    without one."""
    return _scripted(character.call_opener, call_opener_path(character), assets_url)


def _call_poster(character: Character, assets_url: str | None) -> str | None:
    """Her card on the landing page's incoming call (ADR-0003, 2026-10-12), at
    `call_poster_path`, listed without asking the bucket whether it is there (the page shows
    her colour instead when it is not). None unless the stand assets are served."""
    return f"{assets_url}/{call_poster_path(character)}" if assets_url else None


def _voxels(character: Character, assets_url: str | None) -> str | None:
    """Her voxel figure in the landing page's act about memory (ADR-0003, 2026-10-16), at
    `voxels_path`, listed as her call poster is."""
    return f"{assets_url}/{voxels_path(character)}" if assets_url else None


def _scripted(line: ScriptedLine | None, path: str | None, assets_url: str | None) -> dict | None:
    if line is None:
        return None
    return {
        "ja": line.ja,
        "subtitle": line.subtitle,
        "audio_url": f"{assets_url}/{path}" if assets_url and path else None,
    }


def _unit(character: Character, world: World | None) -> dict:
    """Her unit, its reading in hiragana, and where it is in the units' official order
    (`World.unit_of`); all None without the world setting, or if its sources could not
    agree on her unit."""
    unit, order = (world.unit_of(character.name) if world else None) or (None, None)
    reading = world.units[order].reading if world and order is not None else None
    return {"unit": unit, "unit_reading": reading, "unit_order": order}


def display_characters(characters: Iterable[Character], world: World | None, assets_url: str | None) -> list[dict]:
    listed = in_idol_order(characters)
    return [
        {
            "id": c.id,
            "name": c.name,
            "intro": c.intro,
            "avatar_url": f"/api/characters/{c.id}/avatar" if c.avatar else None,
            "scsp_avatar_url": f"/api/characters/{c.id}/scsp-avatar" if c.scsp_avatar else None,
            "color": c.color,
            "birthday": f"{c.birthday.month:02}-{c.birthday.day:02}",
            "background_url": f"/api/characters/{c.id}/background" if c.background else None,
            "stand": _stand(c, assets_url),
            "idol_id": c.idol_id,
            "name_zh": c.name_zh,
            "name_kana": c.name_kana,
            "name_romaji": c.name_romaji,
            **_unit(c, world),
            "greeting": _greeting(c, assets_url),
            "call_opener": _call_opener(c, assets_url),
            "call_poster_url": _call_poster(c, assets_url),
            "voxels_url": _voxels(c, assets_url),
        }
        for c in listed
    ]
