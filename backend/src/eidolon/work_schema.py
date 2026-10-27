"""Explicit offline v0/v1/v2 -> v3 migration. Python remains the only SQLite writer.

The version covers conversation/memory scope and durable grant outcomes; other SC
tables retain their layouts. Existing databases are never upgraded at startup.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from eidolon.database import connect, tables

VERSION = 3
IDENTIFIER = re.compile(r"[a-z0-9_-]+\Z")
CONVERSATION_COLUMNS = {"id", "user_id", "character_id", "created_at", "title"}
TRIGGERS = {"conversation_scope_required", "conversation_identity_immutable",
            "conversation_no_replace", "message_insert_probe", "message_no_replace", "message_parent_immutable"}


def execute_schema(db: sqlite3.Connection, script: str) -> None:
    """Execute trusted DDL without executescript's implicit transaction commit."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete schema statement")


def _check_legacy_columns(db: sqlite3.Connection, table: str) -> None:
    required = {"user_id", "character_id", "created_at"} if table == "conversations" else {"conversation_id", "role", "created_at"}
    for _, name, kind, not_null, default, primary in db.execute(f"PRAGMA table_info({table})"):
        expected_type = "INTEGER" if table == "messages" and name in ("id", "out_of_line") else "REAL" if name == "hurt" else "TEXT"
        if kind.upper() != expected_type or bool(not_null) != (name in required) or default is not None or primary != int(name == "id"):
            raise RuntimeError("unsupported legacy column constraints")
    if table == "messages":
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='messages' AND type='table'").fetchone()[0]
        compact = re.sub(r"\s+", "", re.sub(r"--[^\n]*", "", sql).lower())
        if "idintegerprimarykeyautoincrement" not in compact or "check(rolein('user','character'))" not in compact:
            raise RuntimeError("unsupported legacy message constraints")


def version(db: sqlite3.Connection) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def require_ready(db: sqlite3.Connection) -> None:
    current = version(db)
    if current in (1, 2):
        raise RuntimeError("legacy database requires offline migration and verified backup")
    if current not in (0, VERSION):
        raise RuntimeError("unsupported database schema version; use matching application/backup")
    if "conversations" not in tables(db):
        if current:
            raise RuntimeError("invalid versioned database schema")
        return
    if current == 0:
        raise RuntimeError("legacy database requires offline migration and verified backup")
    _require_scoped(db)
    from eidolon.grant_schema import require_ready as require_grants
    require_grants(db)


def _require_scoped(db: sqlite3.Connection) -> None:
    columns = {r[1] for r in db.execute("PRAGMA table_info(conversations)")}
    triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    views = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    if columns != CONVERSATION_COLUMNS | {"work_id"} or not TRIGGERS <= triggers or "sc_conversations" not in views or "message_insert_guard" not in tables(db):
        raise RuntimeError("invalid versioned conversation schema")
    from eidolon.memory_schema import require_ready as require_memory
    require_memory(db)


def validate_legacy(db: sqlite3.Connection) -> None:
    if version(db) != 0:
        raise RuntimeError("expected legacy schema version 0")
    columns = {r[1] for r in db.execute("PRAGMA table_info(conversations)")}
    if columns not in (CONVERSATION_COLUMNS, CONVERSATION_COLUMNS - {"title"}):
        raise RuntimeError("unsupported legacy conversation schema; no automatic repair")
    required = {"id", "conversation_id", "role", "text", "ja", "zh", "segments", "created_at", "affinity", "hurt", "out_of_line"}
    if {r[1] for r in db.execute("PRAGMA table_info(messages)")} != required:
        raise RuntimeError("unsupported legacy message schema")
    if not {"users", "conversations", "messages"} <= tables(db):
        raise RuntimeError("missing legacy tables")
    if {"own_keys", "access_keys"} & tables(db):
        raise RuntimeError("unsupported older account schema; use matching legacy migration first")
    for table, reference in (("conversations", ("users", "user_id", "id")),
                             ("messages", ("conversations", "conversation_id", "id"))):
        foreign_keys = {(r[2], r[3], r[4]) for r in db.execute(f"PRAGMA foreign_key_list({table})")}
        primary = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[5]]
        if reference not in foreign_keys or primary != ["id"]:
            raise RuntimeError("unsupported legacy identity constraints")
        _check_legacy_columns(db, table)
    for table, column, parent in (("conversations", "user_id", "users"), ("messages", "conversation_id", "conversations")):
        if db.execute(f"SELECT 1 FROM {table} c LEFT JOIN {parent} p ON p.id=c.{column} WHERE p.id IS NULL LIMIT 1").fetchone():
            raise RuntimeError("orphan legacy records; no data changed")
    for id, character in db.execute("SELECT id, character_id FROM conversations"):
        if not id or not isinstance(character, str) or not IDENTIFIER.fullmatch(character):
            raise RuntimeError("invalid legacy identity; no data changed")
    if db.execute("SELECT 1 FROM conversations GROUP BY id HAVING count(*) > 1").fetchone():
        raise RuntimeError("duplicate legacy identity")
    present = tables(db)
    for table in ("conversations", "messages", "conversation_events", "reach_outs"):
        if table not in present:
            continue
        for (stored,) in db.execute(f"SELECT created_at FROM {table}"):
            try:
                valid = datetime.fromisoformat(stored).utcoffset() is not None
            except (ValueError, TypeError):
                valid = False
            if not valid:
                raise RuntimeError(f"invalid {table} timestamp; no automatic repair")
    if "memories" in present and db.execute(
        "SELECT 1 FROM memories m JOIN conversations c ON c.id=m.conversation_id WHERE c.user_id!=m.user_id LIMIT 1"
    ).fetchone():
        raise RuntimeError("memory owner/conversation mismatch")
    if "photos" in present and db.execute(
        "SELECT 1 FROM photos p JOIN messages m ON m.id=p.message_id WHERE m.conversation_id!=p.conversation_id LIMIT 1"
    ).fetchone():
        raise RuntimeError("photo message/conversation mismatch")
    check_integrity(db)


def check_integrity(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("SQLite integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("SQLite foreign key check failed")


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _original_data(db: sqlite3.Connection, columns: dict[str, list[str]]) -> dict[str, str]:
    """Bounded-memory comparison of original columns; never log private values."""
    result = {}
    for table, names in columns.items():
        digest = hashlib.sha256()
        info = db.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
        primary = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        # Rebuilding an AUTOINCREMENT table moves its internal sequence row;
        # compare its named high-watermark, not sqlite_sequence's physical rowid.
        order = 'name' if table == "sqlite_sequence" else ','.join(map(_quoted, primary)) or 'rowid'
        selected = ','.join(map(_quoted, names))
        for row in db.execute(f"SELECT {selected} FROM {_quoted(table)} ORDER BY {order}"):
            encoded = repr(tuple(row)).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        result[table] = digest.hexdigest()
    return result


def install(db: sqlite3.Connection, work_ids: tuple[str, ...] = ("sc",)) -> None:
    """In caller's transaction; fresh databases or explicitly backed-up legacy ones only."""
    if "sc" not in work_ids or len(set(work_ids)) != len(work_ids) or any(not IDENTIFIER.fullmatch(w) for w in work_ids):
        raise ValueError("invalid or duplicate trusted work registry")
    db.execute("CREATE TABLE work_scopes (id TEXT PRIMARY KEY NOT NULL)")
    db.executemany("INSERT INTO work_scopes VALUES (?)", [(w,) for w in work_ids])
    if "title" not in {r[1] for r in db.execute("PRAGMA table_info(conversations)")}:
        db.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
    # Nullable ADD avoids rebuilding referenced tables or disabling foreign keys. The
    # insert trigger enforces required scope, with no implicit SC default on new writes.
    db.execute("ALTER TABLE conversations ADD COLUMN work_id TEXT REFERENCES work_scopes(id)")
    db.execute("UPDATE conversations SET work_id='sc'")
    db.execute("CREATE INDEX conversations_by_work ON conversations(user_id, work_id, character_id, created_at)")
    db.execute("CREATE VIEW sc_conversations AS SELECT id,user_id,character_id,created_at,title,rowid AS rowid FROM conversations WHERE work_id='sc'")
    db.execute("""CREATE TRIGGER conversation_scope_required BEFORE INSERT ON conversations
        WHEN NEW.work_id IS NULL OR NOT EXISTS (SELECT 1 FROM work_scopes WHERE id=NEW.work_id)
        OR NEW.id IS NULL OR NEW.user_id IS NULL OR NEW.character_id IS NULL
        OR NEW.character_id='' OR NEW.character_id GLOB '*[^a-z0-9_-]*'
        BEGIN SELECT RAISE(ABORT, 'explicit registered work and local identity required'); END""")
    db.execute("""CREATE TRIGGER conversation_identity_immutable BEFORE UPDATE OF id,user_id,character_id,work_id ON conversations
        WHEN NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id
        OR NEW.character_id IS NOT OLD.character_id OR NEW.work_id IS NOT OLD.work_id
        BEGIN SELECT RAISE(ABORT, 'conversation identity is immutable'); END""")
    db.execute("""CREATE TRIGGER conversation_no_replace BEFORE INSERT ON conversations
        WHEN EXISTS (SELECT 1 FROM conversations WHERE id=NEW.id)
        BEGIN SELECT RAISE(ABORT, 'existing conversation cannot be replaced'); END""")
    db.execute(f"PRAGMA user_version={VERSION}")
    guard_sc_children(db)
    from eidolon.memory_schema import install as install_memories
    install_memories(db)
    from eidolon.grant_schema import install as install_grants
    install_grants(db)


def guard_sc_children(db: sqlite3.Connection) -> None:
    """Unmigrated business modules may only attach records to SC conversations."""
    present = tables(db)
    for table in ("conversation_events", "photos", "calls", "reach_outs", "reach_out_schedules"):
        if table not in present:
            continue
        owner = " AND c.user_id=NEW.user_id" if table == "memories" else ""
        for event in ("INSERT", "UPDATE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_sc_{event.lower()} BEFORE {event} ON {table}
                WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations c WHERE c.id=NEW.conversation_id AND c.work_id='sc'{owner})
                BEGIN SELECT RAISE(ABORT, 'business record requires SC conversation'); END""")
    if "messages" in present:
        # BEFORE INSERT sees -1 for an unallocated rowid, indistinguishable from an
        # explicit -1. Capture the possible conflict, then compare the allocated ID
        # AFTER INSERT. ABORT restores the original row even for OR REPLACE. SQLite
        # serializes writers; this scratch state is transaction-local in effect.
        # Clear before each row too: OR IGNORE may skip the AFTER trigger.
        db.execute("CREATE TABLE IF NOT EXISTS message_insert_guard (id INTEGER PRIMARY KEY)")
        db.execute("""CREATE TRIGGER IF NOT EXISTS message_insert_probe BEFORE INSERT ON messages
            BEGIN
                DELETE FROM message_insert_guard;
                INSERT INTO message_insert_guard SELECT id FROM messages WHERE id=NEW.id;
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS message_no_replace AFTER INSERT ON messages
            BEGIN
                SELECT RAISE(ABORT, 'existing message cannot be replaced')
                    WHERE EXISTS (SELECT 1 FROM message_insert_guard WHERE id=NEW.id);
                DELETE FROM message_insert_guard;
            END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS message_parent_immutable BEFORE UPDATE OF conversation_id ON messages
            WHEN NEW.conversation_id IS NOT OLD.conversation_id
            BEGIN SELECT RAISE(ABORT, 'message parent is immutable'); END""")
    if "photos" in present:
        for event in ("INSERT", "UPDATE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS photos_message_{event.lower()} BEFORE {event} ON photos
                WHEN NOT EXISTS (SELECT 1 FROM messages m WHERE m.id=NEW.message_id AND m.conversation_id=NEW.conversation_id)
                BEGIN SELECT RAISE(ABORT, 'photo message/conversation mismatch'); END""")


def migrate(path: Path, backup: Path, work_ids: tuple[str, ...] = ("sc",)) -> dict:
    """Stop all services first. Lock out writers while backing up and migrating.

    Online backup API captures committed WAL too; never copy a live .db file. An
    existing backup is never overwritten. Transaction rollback leaves the old schema.
    """
    if not path.is_file() or path.resolve() == backup.resolve():
        raise ValueError("source must exist and backup must be a different new file")
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        if version(db) == VERSION:
            require_ready(db)
            check_integrity(db)
            return {"version": VERSION, "already_migrated": True}
        previous = version(db)
        if previous == 2:
            _require_scoped(db)
            check_integrity(db)
        elif previous == 1:
            triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            if not TRIGGERS <= triggers or "work_id" not in {r[1] for r in db.execute("PRAGMA table_info(conversations)")}:
                raise RuntimeError("invalid v1 conversation schema")
            check_integrity(db)
        else:
            validate_legacy(db)
        counts = {t: db.execute(f'SELECT count(*) FROM {_quoted(t)}').fetchone()[0]
                  for t in sorted(tables(db)) if not t.startswith("sqlite_")}
        columns = {t: [r[1] for r in db.execute(f"PRAGMA table_info({_quoted(t)})")] for t in counts}
        if "sqlite_sequence" in tables(db):
            columns["sqlite_sequence"] = ["name", "seq"]
        original = _original_data(db, columns)
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        reader = connect(path)
        try:
            copy = connect(backup)
            try:
                reader.backup(copy)
                check_integrity(copy)
                if _original_data(copy, columns) != original:
                    raise RuntimeError("backup contents differ from locked source")
            finally:
                copy.close()
        finally:
            reader.close()
        with backup.open("rb") as saved:
            os.fsync(saved.fileno())
        if previous in (1, 2):
            from eidolon.memory_schema import install as install_memories
            from eidolon.grant_schema import install as install_grants
            if previous == 1:
                install_memories(db)
            install_grants(db)
            db.execute(f"PRAGMA user_version={VERSION}")
        else:
            install(db, work_ids)
        require_ready(db)
        if previous == 0 and db.execute("SELECT 1 FROM conversations WHERE work_id IS NOT 'sc' LIMIT 1").fetchone():
            raise RuntimeError("SC backfill verification failed")
        check_integrity(db)
        if _original_data(db, columns) != original:
            raise RuntimeError("migration changed original data; transaction rolled back")
        db.commit()
        return {"version": VERSION, "already_migrated": False, "counts": counts,
                "integrity_check": "ok", "foreign_key_violations": 0}
    finally:
        db.rollback()
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="confirm all application and maintenance writers are stopped")
    parser.add_argument("--registry", type=Path, help="trusted Go /api/works JSON captured by the deployer; never client input")
    args = parser.parse_args()
    if not args.offline:
        parser.error("--offline is required: stop all application and maintenance processes")
    works = ("sc",)
    if args.registry:
        registry = json.loads(args.registry.read_text())
        works = tuple(w["work_id"] for w in registry["works"])
    print(json.dumps(migrate(args.database, args.backup, works), sort_keys=True))


if __name__ == "__main__":
    main()
