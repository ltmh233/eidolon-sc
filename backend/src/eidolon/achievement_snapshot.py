"""Authenticated SC achievement SQL scalars and current rules; no progress projection."""
import base64
import json
import sqlite3
import struct
import sys
import unicodedata
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from eidolon import achievements
from eidolon.affinity_auth import core_service_auth
from eidolon.assembly import Backend
from eidolon.sc_scope import sc_request
from eidolon.sessions import UserId
from eidolon.store import comparison_order

_CONTRACT = 'sc-character-achievements-v1'


class UnsupportedContract(ValueError):
    """A trusted deployment cannot be represented by this version."""


def _scalar(value: object) -> list[str]:
    if type(value) is int:
        return ['i', str(value)]
    if type(value) is float:
        return ['f', struct.pack('>d', value).hex()]
    if isinstance(value, str):
        return ['s', value]
    if isinstance(value, bytes):
        return ['b', base64.b64encode(value).decode('ascii')]
    raise UnsupportedContract()


def _decimal(value: int) -> str:
    try:
        return str(value)
    except ValueError:
        # Python's configured decimal conversion limit also constrains rule serialization.
        raise UnsupportedContract() from None


def _rules() -> tuple[list, list]:
    if sys.version_info[:2] != (3, 11) or unicodedata.unidata_version != '14.0.0':
        raise UnsupportedContract()
    digits = sys.get_int_max_str_digits()
    if digits != 0 and digits < 640:
        raise UnsupportedContract()
    tiers = achievements.TIERS_REACHED
    cards = achievements.COMPLETE_RESET_CARDS
    if not isinstance(tiers, (tuple, list)) or not all(isinstance(t, str) for t in tiers) or type(cards) is not int:
        raise UnsupportedContract()
    if not isinstance(achievements.PER_CHARACTER, (tuple, list)):
        raise UnsupportedContract()
    definitions, seen = [], set()
    for rule in achievements.PER_CHARACTER:
        identifier, goals = getattr(rule, 'id', None), getattr(rule, 'stages', None)
        if not isinstance(goals, (tuple, list)):
            raise UnsupportedContract()
        if not isinstance(identifier, str) or not identifier or identifier == 'complete' or identifier in seen:
            raise UnsupportedContract()
        seen.add(identifier)
        stages = []
        for stage in goals:
            goal, draws = getattr(stage, 'goal', None), getattr(stage, 'draws', None)
            if type(goal) is not int or type(draws) is not int:
                raise UnsupportedContract()
            stages.append([_decimal(goal), _decimal(draws)])
        definitions.append(['achievement', identifier, stages])
    return ['rules', list(tiers), _decimal(cards)], definitions


def _read(db: sqlite3.Connection, user: str, character: str) -> tuple[list, list]:
    counts = db.execute('SELECT achievement,count FROM achievement_progress WHERE user_id=? AND character_id=?',
                        (user, character)).fetchall()
    stages = db.execute('SELECT achievement,stage,unlocked_at FROM achievement_stages WHERE user_id=? AND character_id=?',
                        (user, character)).fetchall()
    normalized = []
    # Like the old _unlocked, parse every old row before any progress is shown.
    for key, stage, value in stages:
        normalized.append(_stage(key, stage, value))
    return [['count', _scalar(k), _scalar(v)] for k, v in counts], normalized


def _stage(key: object, stage: object, value) -> list:
    parsed = datetime.fromisoformat(value)
    iso = parsed.isoformat()
    order = comparison_order(iso)
    return ['stage', _scalar(key), _scalar(stage), [iso, order['aware'], str(order['micros'])]]


def add_achievement_snapshot(app: FastAPI, backend: Backend) -> None:
    require_core = core_service_auth(backend.settings.core_service_token_path)

    @app.get('/api/_core/achievements/{character_id}', include_in_schema=False,
             dependencies=[Depends(require_core), Depends(sc_request)])
    async def snapshot(character_id: str, user_id: UserId):
        if character_id not in backend.characters:
            raise HTTPException(404, '角色不存在')
        enabled = backend.config.affinity_on
        headers = {'Cache-Control': 'no-store'}
        if not enabled:
            rows = [['meta', 1, 'sc', character_id, False, 0, 0, 0, None], ['end']]
        else:
            try:
                rules, definitions = _rules()
                counts, stages = await backend.database.read(_read, user_id, character_id)
                rows = [['meta', 1, 'sc', character_id, True, len(definitions), len(counts), len(stages),
                         ['py311-sql-v1', sys.get_int_max_str_digits()]], rules, *definitions, *counts, *stages, ['end']]
                # Encoding also precedes the successful response headers.
                encoded = [(json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8') for r in rows]
            except UnsupportedContract:
                return JSONResponse({'contract': _CONTRACT, 'error': 'unsupported_contract'}, status_code=503, headers=headers)
            except (sqlite3.Error, TypeError, ValueError, OverflowError):
                return JSONResponse({'contract': _CONTRACT, 'error': 'legacy_read'}, status_code=500, headers=headers)
            return StreamingResponse(iter(encoded), media_type='application/x-ndjson', headers=headers)
        encoded = [(json.dumps(r, separators=(',', ':')) + '\n').encode() for r in rows]
        return StreamingResponse(iter(encoded), media_type='application/x-ndjson', headers=headers)
