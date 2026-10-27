"""Offline migration boundary: only synthetic legacy SQLite files are used."""
import sqlite3
from pathlib import Path

import pytest

from eidolon.data_access import Database


def legacy(path: Path):
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/sc-store-v0.sql").read_text())
        db.execute("INSERT INTO users (id,name,created_at) VALUES ('u','test','2020-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO conversations VALUES ('c','u','removed_character','2020-01-02T00:00:00+00:00','title')")
        db.execute("INSERT INTO messages (id,conversation_id,role,text,created_at) VALUES (7,'c','user','原文','2020-01-03T00:00:00+00:00')")


@pytest.mark.parametrize("historical_id", [-1, -8, 7])
def test_migrated_message_ids_allow_append_but_reject_replace(tmp_path, historical_id):
    from eidolon.work_schema import migrate
    path = tmp_path / "history.db"
    legacy(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE messages SET id=?", (historical_id,))
    migrate(path, tmp_path / "backup.db", ("sc", "demo"))
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO users (id,name,created_at) VALUES ('other','other','2020-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO conversations (id,user_id,character_id,created_at,work_id) VALUES ('foreign','other','mano','2020-01-01T00:00:00+00:00','demo')")
        appended = db.execute("INSERT INTO messages (conversation_id,role,text,created_at) VALUES ('c','user','new','2020-01-04T00:00:00+00:00')").lastrowid
        assert appended != historical_id
        before = db.execute("SELECT * FROM messages ORDER BY id").fetchall()
        for target in ("c", "foreign"):
            with pytest.raises(sqlite3.IntegrityError, match="existing message cannot be replaced"):
                db.execute("INSERT OR REPLACE INTO messages (id,conversation_id,role,text,created_at) VALUES (?,?,'user','replacement','2020-01-04T00:00:00+00:00')", (historical_id, target))
            assert db.execute("SELECT * FROM messages ORDER BY id").fetchall() == before, target
        # IGNORE skips AFTER INSERT; the next insert must not inherit its probe.
        db.execute("INSERT OR IGNORE INTO messages (id,conversation_id,role,text,created_at) VALUES (?,'c','user','ignored','2020-01-04T00:00:00+00:00')", (historical_id,))
        db.execute("INSERT INTO messages (conversation_id,role,text,created_at) VALUES ('c','user','batch A','2020-01-04T00:00:00+00:00'), ('c','user','batch B','2020-01-04T00:00:00+00:00')")
        assert db.execute("SELECT text FROM messages WHERE id=?", (historical_id,)).fetchone() == ("原文",)
        assert db.execute("SELECT count(*) FROM messages").fetchone() == (4,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_old_database_is_not_migrated_by_application_start(tmp_path):
    path = tmp_path / "old.db"
    legacy(path)
    with pytest.raises(RuntimeError, match="offline"):
        Database(path)
    with sqlite3.connect(path) as db:
        assert "work_id" not in {r[1] for r in db.execute("PRAGMA table_info(conversations)")}
        assert db.execute("SELECT text FROM messages").fetchone() == ("原文",)


def test_offline_migration_preserves_records_and_rolls_back(tmp_path, monkeypatch):
    from eidolon import work_schema
    path, backup = tmp_path / "old.db", tmp_path / "backup.db"
    legacy(path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM messages").fetchall()
    original = work_schema.install

    def fail(db, works):
        original(db, works)
        raise RuntimeError("injected after schema and backfill")

    with monkeypatch.context() as patch:
        patch.setattr(work_schema, "install", fail)
        with pytest.raises(RuntimeError, match="injected"):
            work_schema.migrate(path, backup, ("sc", "demo"))
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT * FROM messages").fetchall() == before
        assert "work_id" not in {r[1] for r in db.execute("PRAGMA table_info(conversations)")}
    result = work_schema.migrate(path, tmp_path / "second.db", ("sc", "demo"))
    assert result["counts"]["messages"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM messages").fetchall() == before
        assert db.execute("SELECT id,character_id,work_id FROM conversations").fetchone() == ("c", "removed_character", "sc")
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO conversations (id,user_id,character_id,created_at) VALUES ('bad','u','mano','date')")
    assert work_schema.migrate(path, backup)["already_migrated"]
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT * FROM messages").fetchall() == before


@pytest.mark.parametrize("fault", ["unknown-version", "missing-parent", "null-id", "invalid-character", "missing-constraint", "missing-column", "naive-timestamp"])
def test_invalid_history_is_rejected_without_repair(tmp_path, fault):
    from eidolon.work_schema import migrate
    path = tmp_path / "bad.db"
    legacy(path)
    with sqlite3.connect(path) as db:
        if fault == "unknown-version":
            db.execute("PRAGMA user_version=99")
        elif fault == "missing-parent":
            db.execute("DELETE FROM users")
        elif fault == "null-id":
            db.execute("UPDATE conversations SET id=NULL")
        elif fault == "invalid-character":
            db.execute("UPDATE conversations SET character_id='demo:mano'")
        elif fault == "missing-column":
            db.execute("ALTER TABLE conversations ADD COLUMN unexpected TEXT")
        elif fault == "naive-timestamp":
            db.execute("UPDATE messages SET created_at='2020-01-03'")
        else:
            db.execute("ALTER TABLE messages RENAME TO before_messages")
            db.execute("CREATE TABLE messages AS SELECT * FROM before_messages")
        before = list(db.iterdump())
    with pytest.raises(RuntimeError):
        migrate(path, tmp_path / "backup.db")
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
    assert not (tmp_path / "backup.db").exists()


def test_wal_backup_and_existing_backup_protection(tmp_path):
    from eidolon.work_schema import migrate
    path, backup = tmp_path / "wal.db", tmp_path / "backup.db"
    legacy(path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO messages (conversation_id,role,text,created_at) VALUES ('c','user','WAL-only','2020-01-04T00:00:00+00:00')")
        writer.commit()
        assert Path(str(path) + "-wal").stat().st_size > 0
        migrate(path, backup)
        with sqlite3.connect(backup) as copied:
            assert copied.execute("SELECT text FROM messages ORDER BY id").fetchall() == [("原文",), ("WAL-only",)]
    finally:
        writer.close()
    other = tmp_path / "other.db"
    legacy(other)
    with pytest.raises(FileExistsError):
        migrate(other, backup)
    with sqlite3.connect(other) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)


def test_backup_restore_requires_matching_schema_and_application(tmp_path):
    from eidolon.work_schema import migrate
    from eidolon.restore import restore
    from tests.fakes import FakeClock
    path, backup = tmp_path / "live.db", tmp_path / "before.db"
    legacy(path)
    migrate(path, backup)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            # The old application cannot silently write unscoped conversations.
            db.execute("INSERT INTO conversations (id,user_id,character_id,created_at) VALUES ('old','u','mano','date')")
    restore(backup, path, FakeClock())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        assert db.execute("SELECT text FROM messages").fetchall() == [("原文",)]
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with pytest.raises(RuntimeError, match="offline"):
        Database(path)


async def test_full_schema_integrity_and_audit(make_client, settings):
    import json
    import os
    from tests.fakes import FakeLLM, FakeEmbedder, FakeJudge, FakeReranker
    make_client(FakeLLM(), embedder=FakeEmbedder(), retrieval_judge=FakeJudge(), reranker=FakeReranker())
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        definitions = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        if destination := os.environ.get("EIDOLON_SCHEMA_AUDIT"):
            Path(destination).write_text(json.dumps(definitions, ensure_ascii=False, indent=2))
        print(f"SCHEMA version=3 objects={len(definitions)} integrity=ok foreign_keys=0")


def test_existing_trigger_cannot_silently_change_history(tmp_path):
    from eidolon.work_schema import migrate
    path = tmp_path / "trigger.db"
    legacy(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER old_trigger AFTER UPDATE ON conversations BEGIN UPDATE messages SET text='changed'; END")
        before = list(db.iterdump())
    with pytest.raises(RuntimeError, match="original data"):
        migrate(path, tmp_path / "before.db")
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


def test_unknown_version_and_broken_current_schema_refuse_startup(tmp_path):
    from eidolon.work_schema import migrate
    path = tmp_path / "unknown.db"
    legacy(path)
    migrate(path, tmp_path / "before.db")
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="unsupported"):
        Database(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=3")
        db.execute("DROP VIEW sc_conversations")
    with pytest.raises(RuntimeError, match="invalid"):
        Database(path)


async def test_fresh_initialization_failure_is_retryable(tmp_path, monkeypatch):
    import eidolon.store
    path = tmp_path / "fresh.db"
    original = eidolon.store.install
    def fail(db):
        original(db)
        raise RuntimeError("fresh initialization injected failure")
    first = Database(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(eidolon.store, "install", fail)
            with pytest.raises(RuntimeError, match="injected"):
                eidolon.store.Store(first)
    finally:
        await first.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    second = Database(path)
    try:
        store = eidolon.store.Store(second)
        user = await store.create_user("retry")
        assert (await store.create_conversation(user.id, "mano")).character_id == "mano"
    finally:
        await second.close()


@pytest.mark.parametrize("removed", ["NOT NULL REFERENCES conversations(id)", "CHECK (role IN ('user', 'character'))", "AUTOINCREMENT"])
def test_weak_legacy_constraints_are_not_accepted(tmp_path, removed):
    from eidolon.work_schema import migrate
    path = tmp_path / "weak.db"
    script = (Path(__file__).parent / "fixtures/sc-store-v0.sql").read_text()
    replacement = "REFERENCES conversations(id)" if removed.startswith("NOT NULL") else ""
    with sqlite3.connect(path) as db:
        db.executescript(script.replace(removed, replacement))
        before = list(db.iterdump())
    with pytest.raises(RuntimeError, match="schema|constraint"):
        migrate(path, tmp_path / "before.db")
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
