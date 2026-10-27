"""Durable anonymous grant outcomes; installed fresh or by offline migration only."""
import re
import sqlite3
from datetime import datetime

_TABLE = 'reset_card_grants'
_INDEX = 'reset_card_grants_by_operation'
_GUARDS = {
    'reset_card_grants_operation_required': """CREATE TRIGGER reset_card_grants_operation_required
        BEFORE INSERT ON reset_card_grants WHEN NEW.operation_id IS NULL OR NEW.operation_id=''
        BEGIN SELECT RAISE(ABORT, 'grant operation id required'); END""",
    'reset_card_grants_immutable': """CREATE TRIGGER reset_card_grants_immutable
        BEFORE UPDATE ON reset_card_grants
        BEGIN SELECT RAISE(ABORT, 'grant outcomes are immutable'); END""",
    'reset_card_grants_no_replace': """CREATE TRIGGER reset_card_grants_no_replace
        BEFORE INSERT ON reset_card_grants WHEN NEW.operation_id IS NOT NULL AND EXISTS
        (SELECT 1 FROM reset_card_grants WHERE operation_id=NEW.operation_id)
        BEGIN SELECT RAISE(ABORT, 'grant outcome already exists'); END""",
}


def install(db: sqlite3.Connection) -> None:
    """Within the caller's transaction; never repair an unknown historical shape."""
    old = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone()
    if old:
        compact = re.sub(r'\s+', '', old[0]).lower().replace('ifnotexists', '')
        if compact != 'createtablereset_card_grants(granted_attextnotnull,countintegernotnull)':
            raise RuntimeError('unsupported historical grant schema')
        if db.execute(f'PRAGMA index_list({_TABLE})').fetchall() or db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (_TABLE,)
        ).fetchone():
            raise RuntimeError('unsupported historical grant constraints')
        for at, count in db.execute(f'SELECT granted_at,count FROM {_TABLE}'):
            try:
                valid = isinstance(at, str) and datetime.fromisoformat(at).utcoffset() is not None
            except ValueError:
                valid = False
            if not valid or not isinstance(count, int) or count < 0:
                raise RuntimeError('invalid historical grant outcome')
        db.execute(f'ALTER TABLE {_TABLE} ADD COLUMN operation_id TEXT')
    else:
        db.execute(f'CREATE TABLE {_TABLE} (granted_at TEXT NOT NULL,count INTEGER NOT NULL,operation_id TEXT)')
    db.execute(f'CREATE UNIQUE INDEX {_INDEX} ON {_TABLE}(operation_id) WHERE operation_id IS NOT NULL')
    for ddl in _GUARDS.values():
        db.execute(ddl)


def require_ready(db: sqlite3.Connection) -> None:
    columns = [(r[1], r[2], r[3], r[4], r[5]) for r in db.execute(f'PRAGMA table_info({_TABLE})')]
    expected = [('granted_at', 'TEXT', 1, None, 0), ('count', 'INTEGER', 1, None, 0),
                ('operation_id', 'TEXT', 0, None, 0)]
    objects = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE tbl_name=?", (_TABLE,)))
    index = db.execute(f'PRAGMA index_list({_TABLE})').fetchall()
    if columns != expected or not any(r[1] == _INDEX and r[2] == 1 and r[4] == 1 for r in index):
        raise RuntimeError('invalid versioned grant schema')
    expected_index = f'CREATE UNIQUE INDEX {_INDEX} ON {_TABLE}(operation_id) WHERE operation_id IS NOT NULL'
    for name, ddl in {**_GUARDS, _INDEX: expected_index}.items():
        if re.sub(r'\s+', '', objects.get(name, '')).lower() != re.sub(r'\s+', '', ddl).lower():
            raise RuntimeError('invalid versioned grant constraints')
