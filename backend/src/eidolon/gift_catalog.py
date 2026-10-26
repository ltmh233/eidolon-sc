"""The gift catalog (GLOSSARY.md「礼物」), loaded from the configuration beside the
characters'.

It is configuration, not data: the maintainer keeps it in the repository as they keep the
characters, and neither `.env` nor the admin console changes it. Each gift has its
identifier (also its illustration's file name in the frontend's static files, and what
backpacks and gift records keep), its name, its category and its rarity. A gift is never
deleted once out, only retired: no draw gives it any more, but whoever has one still sees
it and can give it. Every rarity needs a gift not retired, or draws of it would come up
empty. What each rarity raises affinity by and how likely a draw is to give it are with
affinity's other numbers (`affinity`).

Anything wrong in it keeps the backend from starting (`GiftCatalogError`), as do liked
gifts in the character configuration that are not right by it (`check_liked_gifts`).
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from eidolon.characters import Character, CharacterConfigError

Category = Literal["food", "drink", "flower", "trinket"]
"""吃的、饮品、花草、小物件."""

Rarity = Literal["common", "uncommon", "rare"]
"""普通、少见、稀有, from the commonest."""

GIFTS_FILE = "gifts.toml"
"""The file's name, in the directory of the character configuration."""

MAX_LIKED = 3
"""The most gifts a character likes."""

_VALID_ID = re.compile(r"[a-z0-9_-]+")


class GiftCatalogError(RuntimeError):
    """The gift catalog is missing or broken."""


@dataclass(frozen=True)
class Gift:
    id: str
    """Also its illustration's file name, and what backpacks and gift records keep."""
    name: str
    """Its everyday name, in Chinese."""
    category: Category
    rarity: Rarity
    retired: bool = False
    """No draw gives it any more."""

    def described(self) -> dict:
        """As the gifts page reads it."""
        return {
            "id": self.id, "name": self.name, "category": self.category,
            "rarity": self.rarity, "retired": self.retired,
        }


def gifts_path(characters_path: Path) -> Path:
    """Where the gift catalog is: beside the character configuration."""
    return characters_path.parent / GIFTS_FILE


def load_gift_catalog(path: Path) -> dict[str, Gift]:
    """The gifts in the catalog at `path`, keyed by identifier in file order. Raises
    GiftCatalogError if it is missing or broken."""
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GiftCatalogError(f"找不到礼物目录 {path}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise GiftCatalogError(f"礼物目录 {path} 格式有误：{e}") from None
    entries = config.get("gifts", [])
    return gifts_from_entries(entries)


def gifts_from_entries(entries: list[dict]) -> dict[str, Gift]:
    """Materialize normalized Go definitions, sharing validation with source editing tools."""
    if not isinstance(entries, list):
        raise GiftCatalogError("礼物目录的 gifts 应当是条目列表")

    catalog: dict[str, Gift] = {}
    for entry in entries:
        gift = _parse_gift(entry)
        if gift.id in catalog:
            raise GiftCatalogError(f"礼物标识 {gift.id} 重复")
        catalog[gift.id] = gift
    for rarity in get_args(Rarity):
        if not any(g.rarity == rarity and not g.retired for g in catalog.values()):
            raise GiftCatalogError(f"礼物目录里稀有度 {rarity} 没有一件没下架的礼物，这一档抽不出东西")
    return catalog


def check_liked_gifts(characters: Mapping[str, Character], catalog: Mapping[str, Gift]) -> None:
    """Raises CharacterConfigError unless every character likes one to `MAX_LIKED` gifts,
    all in `catalog`, no one twice, and not all of them rare: a draw seldom gives a rare
    one, and a character should not have to wait for it."""
    for c in characters.values():
        liked = c.liked_gifts
        if not liked:
            raise CharacterConfigError(f"角色 {c.id} 没有配 liked_gifts（喜欢的礼物，1–{MAX_LIKED} 件）")
        if len(liked) > MAX_LIKED:
            raise CharacterConfigError(
                f"角色 {c.id} 的 liked_gifts 有 {len(liked)} 件，最多 {MAX_LIKED} 件"
            )
        if len(set(liked)) < len(liked):
            raise CharacterConfigError(f"角色 {c.id} 的 liked_gifts 里有重复的礼物")
        if unknown := [g for g in liked if g not in catalog]:
            raise CharacterConfigError(
                f"角色 {c.id} 的 liked_gifts 里的 {'、'.join(unknown)} 不在礼物目录里"
            )
        if all(catalog[g].rarity == "rare" for g in liked):
            raise CharacterConfigError(
                f"角色 {c.id} 的 liked_gifts 全是稀有礼物，至少要有一件普通或少见的"
            )


def _parse_gift(entry: object) -> Gift:
    if not isinstance(entry, dict):
        raise GiftCatalogError(f"礼物目录里的 {entry!r} 应当是一个 [[gifts]] 段")
    label = entry.get("id", "（没有标识）")
    missing = [f for f in ("id", "name", "category", "rarity") if f not in entry]
    if missing:
        raise GiftCatalogError(f"礼物 {label} 缺少 {'、'.join(missing)}")
    id, name, category, rarity = entry["id"], entry["name"], entry["category"], entry["rarity"]
    if not isinstance(id, str) or not _VALID_ID.fullmatch(id):
        raise GiftCatalogError(f"礼物标识 {id} 只能用小写字母、数字、- 和 _")
    if not isinstance(name, str) or not name.strip():
        raise GiftCatalogError(f"礼物 {id} 的 name 应当是一段文字")
    _one_of(category, Category, f"礼物 {id} 的类别")
    _one_of(rarity, Rarity, f"礼物 {id} 的稀有度")
    retired = entry.get("retired", False)
    if not isinstance(retired, bool):
        raise GiftCatalogError(f"礼物 {id} 的 retired 应当是 true 或 false")
    return Gift(id, name, category, rarity, retired)


def _one_of(value: object, choices: object, label: str) -> None:
    """Raises GiftCatalogError unless `value` is one of the `Literal` `choices`."""
    if value not in get_args(choices):
        raise GiftCatalogError(f"{label} {value} 不在 {'、'.join(get_args(choices))} 之中")
