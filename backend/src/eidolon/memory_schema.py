"""Memory v2 DDL and offline preservation of SC memory identities."""
import sqlite3
import re
from datetime import datetime


def install(db: sqlite3.Connection) -> None:
    present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    old = "memories" in present
    has_superseding = False
    if old:
        columns = {r[1] for r in db.execute("PRAGMA table_info(memories)")}
        required = {"id", "user_id", "source_character_id", "conversation_id", "text", "created_at"}
        if columns not in (required, required | {"superseded_by"}):
            raise RuntimeError("unsupported legacy memory schema")
        has_superseding = "superseded_by" in columns
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='memories' AND type='table'").fetchone()[0]
        if re.search(r"\b(check|collate|default|strict)\b|without\s+rowid", sql, re.IGNORECASE):
            raise RuntimeError("unsupported legacy memory constraints")
        if "idintegerprimarykeyautoincrement" not in re.sub(r"\s+", "", sql.lower()):
            raise RuntimeError("unsupported legacy memory identity")
        foreign_rows = list(db.execute("PRAGMA foreign_key_list(memories)"))
        foreign_keys = {(r[2], r[3], r[4]) for r in foreign_rows}
        if foreign_keys != {("users", "user_id", "id"), ("conversations", "conversation_id", "id")}:
            raise RuntimeError("unsupported legacy memory foreign keys")
        if any(tuple(r[5:8]) != ("NO ACTION", "NO ACTION", "NONE") for r in foreign_rows):
            raise RuntimeError("unsupported legacy memory foreign key actions")
        for _, name, kind, not_null, default, primary in db.execute("PRAGMA table_info(memories)"):
            if kind.upper() != ("INTEGER" if name in ("id", "superseded_by") else "TEXT") or default is not None or primary != int(name == "id") or bool(not_null) != (name in {"user_id", "source_character_id", "text", "created_at"}):
                raise RuntimeError("unsupported legacy memory constraints")
        indexes = list(db.execute("PRAGMA index_list(memories)"))
        unique = [tuple(r[2] for r in db.execute(f'PRAGMA index_info("{row[1]}")')) for row in indexes if row[2]]
        if unique != [("user_id", "text")] or any(row[1] != "memories_by_conversation" and row[3] != "u" for row in indexes):
            raise RuntimeError("unsupported legacy memory indexes")
        triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='memories'")}
        if triggers - {"memories_sc_insert", "memories_sc_update"}:
            raise RuntimeError("unsupported legacy memory triggers")
        for text, created, source in db.execute("SELECT text,created_at,source_character_id FROM memories"):
            try:
                valid = datetime.fromisoformat(created).utcoffset() is not None
            except (TypeError, ValueError):
                valid = False
            if not valid or not isinstance(text, str) or not isinstance(source, str):
                raise RuntimeError("invalid legacy memory fields")
        superseding = "m.superseded_by" if has_superseding else "NULL"
        invalid = db.execute(f"""SELECT 1 FROM memories m
            LEFT JOIN users u ON u.id=m.user_id
            LEFT JOIN conversations c ON c.id=m.conversation_id
            LEFT JOIN memories s ON s.id={superseding}
            WHERE u.id IS NULL OR (m.conversation_id IS NOT NULL AND
                (c.id IS NULL OR c.user_id!=m.user_id OR c.work_id!='sc'))
                OR ({superseding} IS NOT NULL AND (s.id IS NULL OR s.user_id!=m.user_id OR s.id=m.id))
            LIMIT 1""").fetchone()
        if invalid:
            raise RuntimeError("invalid legacy memory association")
        if has_superseding and db.execute("""WITH RECURSIVE chain(start,id) AS (
            SELECT id,superseded_by FROM memories UNION SELECT chain.start,m.superseded_by
            FROM chain JOIN memories m ON m.id=chain.id)
            SELECT 1 FROM chain WHERE start=id LIMIT 1""").fetchone():
            raise RuntimeError("cyclic legacy memory superseding")
    if "memory_vectors" in present:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='memory_vectors' AND type='table'").fetchone()[0]
        if re.search(r"\b(check|collate|default|strict|references)\b|without\s+rowid", sql, re.IGNORECASE):
            raise RuntimeError("unsupported legacy vector constraints")
        if {r[1] for r in db.execute("PRAGMA table_info(memory_vectors)")} != {"memory_id", "user_id", "model", "embedding"}:
            raise RuntimeError("unsupported legacy vector schema")
        for _, name, kind, not_null, default, primary in db.execute("PRAGMA table_info(memory_vectors)"):
            expected = "INTEGER" if name == "memory_id" else "BLOB" if name == "embedding" else "TEXT"
            if kind.upper() != expected or not not_null or default is not None or primary != {"memory_id": 1, "model": 2}.get(name, 0):
                raise RuntimeError("unsupported legacy vector constraints")
        indexes = list(db.execute("PRAGMA index_list(memory_vectors)"))
        if any(row[3] != "pk" and row[1] != "memory_vectors_by_user" for row in indexes):
            raise RuntimeError("unsupported legacy vector indexes")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='memory_vectors'").fetchone():
            raise RuntimeError("unsupported legacy vector triggers")
        if not old or db.execute("""SELECT 1 FROM memory_vectors v LEFT JOIN memories m ON m.id=v.memory_id
                WHERE m.id IS NULL OR m.user_id!=v.user_id LIMIT 1""").fetchone():
            raise RuntimeError("invalid legacy memory vector association")
    sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='memories'").fetchone()
    db.execute("""CREATE TABLE memories_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id),
        source_character_id TEXT NOT NULL,
        conversation_id TEXT REFERENCES conversations(id),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        superseded_by INTEGER,
        work_id TEXT NOT NULL REFERENCES work_scopes(id),
        UNIQUE(user_id,work_id,text)
    )""")
    if old:
        superseding = "superseded_by" if has_superseding else "NULL"
        db.execute(f"INSERT INTO memories_v2 SELECT id,user_id,source_character_id,conversation_id,text,created_at,{superseding},'sc' FROM memories")
        db.execute("DROP TABLE memories")
    db.execute("ALTER TABLE memories_v2 RENAME TO memories")
    if sequence:
        db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='memories'", sequence)
    elif old:
        # Zero-row INSERT SELECT creates seq=0 although a never-written old table
        # had no sequence row. Preserve that distinction, not just the next ID.
        db.execute("DELETE FROM sqlite_sequence WHERE name='memories'")
    db.execute("CREATE INDEX memories_by_conversation ON memories(conversation_id)")
    db.execute("CREATE INDEX memories_by_scope ON memories(user_id,work_id,id)")
    db.execute("""CREATE TABLE IF NOT EXISTS memory_vectors (
        memory_id INTEGER NOT NULL, user_id TEXT NOT NULL, model TEXT NOT NULL,
        embedding BLOB NOT NULL, PRIMARY KEY(memory_id,model))""")
    db.execute("CREATE INDEX IF NOT EXISTS memory_vectors_by_user ON memory_vectors(user_id,model)")
    for event in ("INSERT", "UPDATE"):
        db.execute(f"""CREATE TRIGGER memory_scope_{event.lower()} BEFORE {event} ON memories
            WHEN NOT EXISTS (SELECT 1 FROM work_scopes WHERE id=NEW.work_id)
            OR NOT EXISTS (SELECT 1 FROM users WHERE id=NEW.user_id)
            OR (NEW.conversation_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM conversations WHERE id=NEW.conversation_id AND user_id=NEW.user_id AND work_id=NEW.work_id))
            OR (NEW.superseded_by IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM memories WHERE id=NEW.superseded_by AND user_id=NEW.user_id AND work_id=NEW.work_id))
            OR EXISTS (WITH RECURSIVE chain(id) AS (SELECT NEW.superseded_by UNION
                SELECT m.superseded_by FROM memories m JOIN chain ON m.id=chain.id)
                SELECT 1 FROM chain WHERE id=NEW.id AND '{event}'='UPDATE')
            BEGIN SELECT RAISE(ABORT, 'memory scope association mismatch'); END""")
        db.execute(f"""CREATE TRIGGER memory_vector_{event.lower()} BEFORE {event} ON memory_vectors
            WHEN NOT EXISTS (SELECT 1 FROM memories WHERE id=NEW.memory_id AND user_id=NEW.user_id)
            BEGIN SELECT RAISE(ABORT, 'memory vector owner mismatch'); END""")
    db.execute("""CREATE TRIGGER memory_vector_identity BEFORE UPDATE OF memory_id,user_id,model ON memory_vectors
        WHEN NEW.memory_id IS NOT OLD.memory_id OR NEW.user_id IS NOT OLD.user_id OR NEW.model IS NOT OLD.model
        BEGIN SELECT RAISE(ABORT, 'memory vector identity is immutable'); END""")
    db.execute("""CREATE TRIGGER memory_identity_immutable BEFORE UPDATE OF id,user_id,work_id ON memories
        WHEN NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id OR NEW.work_id IS NOT OLD.work_id
        BEGIN SELECT RAISE(ABORT, 'memory identity is immutable'); END""")
    db.execute("CREATE TABLE memory_insert_guard (id INTEGER PRIMARY KEY)")
    db.execute("""CREATE TRIGGER memory_insert_probe BEFORE INSERT ON memories BEGIN
        DELETE FROM memory_insert_guard;
        INSERT INTO memory_insert_guard SELECT id FROM memories
            WHERE id=NEW.id OR (user_id=NEW.user_id AND work_id=NEW.work_id AND text=NEW.text);
        END""")
    db.execute("""CREATE TRIGGER memory_no_replace AFTER INSERT ON memories BEGIN
        SELECT RAISE(ABORT, 'existing memory cannot be replaced')
            WHERE EXISTS (SELECT 1 FROM memory_insert_guard g WHERE g.id=NEW.id
                OR NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=g.id));
        DELETE FROM memory_insert_guard;
        END""")
    db.execute("""CREATE TRIGGER memory_delete_links AFTER DELETE ON memories BEGIN
        DELETE FROM memory_vectors WHERE memory_id=OLD.id;
        UPDATE memories SET superseded_by=OLD.superseded_by WHERE superseded_by=OLD.id;
        END""")


def require_ready(db: sqlite3.Connection) -> None:
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {r[1] for r in db.execute("PRAGMA table_info(memories)")}
    triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    guard = list(db.execute("PRAGMA table_info(memory_insert_guard)"))
    if not {"memories", "memory_vectors", "memory_insert_guard"} <= tables or len(guard) != 1 or guard[0][1:3] != ("id", "INTEGER") or guard[0][5] != 1:
        raise RuntimeError("invalid versioned memory schema")
    if columns != {"id", "user_id", "work_id", "source_character_id", "conversation_id", "text", "created_at", "superseded_by"} or not {"memory_scope_insert", "memory_scope_update", "memory_identity_immutable",
            "memory_insert_probe", "memory_no_replace", "memory_delete_links", "memory_vector_insert", "memory_vector_update", "memory_vector_identity"} <= triggers:
        raise RuntimeError("invalid versioned memory schema")
