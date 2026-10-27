"""Real storage boundaries; no product API opens non-SC chat."""
import sqlite3
import asyncio
from pathlib import Path

import pytest

from eidolon.data_access import Database
from eidolon.store import Store
from eidolon.work_schema import migrate
from tests.fakes import FakeClock


def history(path):
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/sc-memory-v1.sql").read_text())


async def test_v1_memory_upgrade_preserves_history_and_work_dedup(tmp_path):
    path = tmp_path / "history.db"
    history(path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM memories ORDER BY id").fetchall()
        vectors = db.execute("SELECT * FROM memory_vectors").fetchall()
    result = migrate(path, tmp_path / "backup.db")
    assert result["version"] == 3
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,user_id,source_character_id,conversation_id,text,created_at,superseded_by FROM memories ORDER BY id").fetchall() == before
        assert db.execute("SELECT * FROM memory_vectors").fetchall() == vectors
    from eidolon.memory import WorkMemories
    db = Database(path)
    try:
        sc = WorkMemories(db, user_id="u", work_id="sc", characters={}, clock=FakeClock())
        demo = WorkMemories(db, user_id="u", work_id="demo", characters={}, clock=FakeClock())
        assert [m.id for m in await sc.entries()] == [17, 9]
        assert await demo.entries() == []
        assert await demo.add("制作人喜欢咖啡", "mano") != 17
        assert await sc.add("制作人喜欢咖啡", "mano") == 17
        await demo.clear()
        assert len(await sc.entries()) == 2
        assert await demo.entries() == []
    finally:
        await db.close()


async def test_unregistered_work_cannot_read_or_write(tmp_path):
    from eidolon.memory import WorkMemories
    db = Database(tmp_path / "fresh.db")
    try:
        store = Store(db)
        user = await store.create_user("test")
        with pytest.raises(ValueError, match="registered"):
            WorkMemories(db, user_id=user.id, work_id="unknown", characters={}, clock=FakeClock())
    finally:
        await db.close()


async def test_candidates_dedup_superseding_and_deletion_are_scoped(tmp_path):
    from eidolon.memory import WorkMemories, Memories
    from tests.fakes import FakeEmbedder, FakeReranker
    db = Database(tmp_path / "scopes.db")
    try:
        store = Store(db)
        a, b = await store.create_user("A"), await store.create_user("B")
        await db.write(lambda c: c.execute("INSERT INTO work_scopes VALUES ('demo')"))
        services = []
        for user, work in ((a.id, "sc"), (a.id, "demo"), (b.id, "sc")):
            embedding, ranking = FakeEmbedder(), FakeReranker()
            service = WorkMemories(db, user_id=user, work_id=work, characters={}, clock=FakeClock(), embedder=embedding, reranker=ranking)
            services.append((service, embedding, ranking))
        ids = [await service.add("共同的猫", "mano") for service, _, _ in services]
        assert len(set(ids)) == 3
        for index, (service, embedder, reranker) in enumerate(services):
            own = await service.add(f"私有猫 {index}", "mano")
            assert await service.add("共同的猫", "other") == ids[index], index
            assert {m.id for m in await service.keywords("猫")} == {ids[index], own}, index
            assert {m.id for m in await service.search("猫")} == {ids[index], own}, index
            assert set(embedder.documents) == {"共同的猫", f"私有猫 {index}"}, index
            assert set(reranker.calls[-1][1]) == {"共同的猫", f"私有猫 {index}"}, index
            foreign = ids[(index+1) % 3]
            assert not await service.delete(foreign), index
            assert not await service.supersede(foreign, own), index
            with pytest.raises(sqlite3.IntegrityError, match="scope"):
                await service.supersede(own, foreign)
            assert await service.supersede(ids[index], own), index
            assert [m.id for m in await service.search("猫")] == [own], index
            assert await service.delete(own), index
            assert [m.id for m in await service.search("猫")] == [ids[index]], index
        sc = Memories(db, {}, FakeClock())
        assert [m.id for m in await sc.entries(a.id)] == [ids[0]]
        await services[1][0].clear()
        assert [m.id for m in await sc.entries(a.id)] == [ids[0]]
        demo_id = await services[1][0].add("注销前 Demo 猫", "mano")
        assert [m.id for m in await services[1][0].search("猫")] == [demo_id]
        await store.delete_user(a.id, [])
        assert await services[0][0].entries() == []
        assert await services[1][0].entries() == []
        assert [m.id for m in await services[2][0].entries()] == [ids[2]]
        assert await db.read(lambda c: c.execute("SELECT count(*) FROM memory_vectors WHERE user_id=?", (a.id,)).fetchone()[0]) == 0
    finally:
        await db.close()


@pytest.mark.parametrize("attack", ["missing-work", "move-work", "foreign-source", "foreign-vector", "replace-negative", "replace-text", "cross-supersede", "vector-move", "cycle"])
async def test_direct_sql_cannot_reassign_memories(tmp_path, attack):
    from eidolon.memory import WorkMemories
    from eidolon.work_conversations import WorkConversations
    db = Database(tmp_path / "sql.db")
    try:
        store = Store(db)
        a, b = await store.create_user("A"), await store.create_user("B")
        await db.write(lambda c: c.execute("INSERT INTO work_scopes VALUES ('demo')"))
        foreign = await WorkConversations(db, user_id=b.id, work_id="demo").create("mano")
        sc = WorkMemories(db, user_id=a.id, work_id="sc", characters={}, clock=FakeClock())
        await db.write(lambda c: c.execute("INSERT INTO memories(id,user_id,work_id,source_character_id,text,created_at) VALUES (-1,?,'sc','mano','original','2020-01-01T00:00:00+00:00')", (a.id,)))
        second = await sc.add("second", "mano")
        other = WorkMemories(db, user_id=b.id, work_id="demo", characters={}, clock=FakeClock())
        other_id = await other.add("other", "mano", foreign.id)
        await db.write(lambda c: c.execute("INSERT INTO memory_vectors VALUES (-1,?,'model',X'00000000')", (a.id,)))
        statements = {
            "missing-work": ("INSERT INTO memories(user_id,source_character_id,text,created_at) VALUES (?,'mano','bad','date')", (a.id,)),
            "move-work": ("UPDATE memories SET work_id='demo' WHERE id=-1", ()),
            "foreign-source": ("UPDATE memories SET conversation_id=? WHERE id=-1", (foreign.id,)),
            "foreign-vector": ("INSERT INTO memory_vectors VALUES (-1,?,'model',X'00000000')", (b.id,)),
            "replace-negative": ("INSERT OR REPLACE INTO memories(id,user_id,work_id,source_character_id,text,created_at) VALUES (-1,?,'demo','mano','stolen','date')", (b.id,)),
            "replace-text": ("INSERT OR REPLACE INTO memories(user_id,work_id,source_character_id,text,created_at) VALUES (?,'sc','mano','original','date')", (a.id,)),
            "cross-supersede": ("UPDATE memories SET superseded_by=? WHERE id=-1", (other_id,)),
            "vector-move": ("UPDATE memory_vectors SET memory_id=?,user_id=? WHERE memory_id=-1", (other_id, b.id)),
            "cycle": ("UPDATE memories SET superseded_by=CASE WHEN id=-1 THEN ? ELSE -1 END WHERE id IN (-1,?)", (second, second)),
        }
        sql, params = statements[attack]
        with pytest.raises(sqlite3.IntegrityError):
            await db.write(lambda c: c.execute(sql, params))
        assert {m.id: m.text for m in await sc.entries()} == {-1: "original", second: "second"}, attack
        assert [m.id for m in await other.entries()] == [other_id], attack
    finally:
        await db.close()


def test_migration_failure_rolls_back_and_backup_is_preserved(tmp_path, monkeypatch):
    import eidolon.memory_schema as schema
    path, backup = tmp_path / "old.db", tmp_path / "backup.db"
    history(path)
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    original = schema.install
    def fail(db):
        original(db)
        raise RuntimeError("injected after memory rebuild")
    with monkeypatch.context() as patch:
        patch.setattr(schema, "install", fail)
        with pytest.raises(RuntimeError, match="injected"):
            migrate(path, backup)
    for label, file in (("rolled back", path), ("backup", backup)):
        with sqlite3.connect(file) as db:
            assert list(db.iterdump()) == before, label
            assert db.execute("PRAGMA user_version").fetchone() == (1,), label
    with pytest.raises(FileExistsError):
        migrate(path, backup)
    migrate(path, tmp_path / "second-backup.db")
    assert migrate(path, backup)["already_migrated"]


@pytest.mark.parametrize("fault", ["vector-owner", "orphan-vector", "supersede", "schema", "timestamp", "weak-vector-schema", "vector-trigger"])
def test_invalid_memory_history_is_not_repaired(tmp_path, fault):
    path = tmp_path / "bad.db"
    history(path)
    with sqlite3.connect(path) as db:
        if fault == "weak-vector-schema":
            db.executescript("CREATE TABLE replacement AS SELECT * FROM memory_vectors; DROP TABLE memory_vectors; ALTER TABLE replacement RENAME TO memory_vectors;")
        sql = {
            "vector-owner": "UPDATE memory_vectors SET user_id='missing'",
            "orphan-vector": "UPDATE memory_vectors SET memory_id=999",
            "supersede": "UPDATE memories SET superseded_by=999 WHERE id=17",
            "schema": "ALTER TABLE memories ADD COLUMN surprise TEXT",
            "timestamp": "UPDATE memories SET created_at='bad'",
            "weak-vector-schema": "SELECT 1",
            "vector-trigger": "CREATE TRIGGER unexpected_vector_write AFTER INSERT ON memory_vectors BEGIN DELETE FROM memory_vectors; END",
        }[fault]
        db.execute(sql)
        before = list(db.iterdump())
    with pytest.raises(RuntimeError):
        migrate(path, tmp_path / "backup.db")
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA user_version").fetchone() == (1,)


async def test_delayed_extraction_keeps_original_scope_after_source_deleted(tmp_path, characters):
    from eidolon.memory import WorkMemories
    from eidolon.characters import load_characters
    from eidolon.work_conversations import WorkConversations
    from eidolon.store import Message
    from tests.fakes import FakeBackgroundLLM, FakeEmbedder, FakeReranker
    from tests.test_shared_memory import memorized
    started, release = asyncio.Event(), asyncio.Event()
    class Delayed(FakeBackgroundLLM):
        async def stream(self, messages, *args, **kwargs):
            started.set()
            await release.wait()
            async for chunk in super().stream(messages, *args, **kwargs):
                yield chunk
    db = Database(tmp_path / "async.db")
    scoped = []
    try:
        store = Store(db)
        user = await store.create_user("A")
        await db.write(lambda c: c.execute("INSERT INTO work_scopes VALUES ('demo')"))
        registry = load_characters(characters.path)
        sc, demo = [WorkMemories(db, user_id=user.id, work_id=w, characters=registry, clock=FakeClock(), embedder=FakeEmbedder(), reranker=FakeReranker()) for w in ("sc", "demo")]
        scoped = [sc, demo]
        conversations = WorkConversations(db, user_id=user.id, work_id="sc")
        original = await conversations.create("mano")
        other = await WorkConversations(db, user_id=user.id, work_id="demo").create("mano")
        await demo.add("Demo 私有猫", "mano", other.id)
        with pytest.raises(ValueError, match="scope"):
            await demo.begin(original, "UTC", FakeBackgroundLLM())
        llm = Delayed(memorized("SC 延迟猫"))
        turn = await sc.begin(original, "UTC", llm)
        turn.write_down([], "我养猫", Message(1, "character", None, "はい", "好", original.created_at))
        await asyncio.wait_for(started.wait(), 2)
        await demo.clear()
        await conversations.delete(original.id)
        release.set()
        records = await sc.entries()
        assert [(m.text, m.conversation_id) for m in records] == [("SC 延迟猫", None)]
        assert await demo.entries() == []
        assert "Demo 私有猫" not in str(llm.prompts)
        # A foreign candidate must never be passed to the scope's reranker.
        foreign_id = await demo.add("Demo 私有猫", "mano", other.id)
        fresh = await conversations.create("kogane")
        turn = await sc.begin(fresh, "UTC", FakeBackgroundLLM())
        assert await turn.find("猫", await demo.entries()) is None
        assert foreign_id != records[0].id
    finally:
        release.set()
        for service in scoped:
            await service.aclose()
        await db.close()


def test_v2_missing_probe_table_refuses_startup(tmp_path):
    path = tmp_path / "broken.db"
    history(path)
    migrate(path, tmp_path / "backup.db")
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE memory_insert_guard")
    with pytest.raises(RuntimeError, match="memory schema"):
        Database(path)


@pytest.mark.parametrize("shape", ["empty", "absent"])
def test_v1_without_memory_records_upgrades_without_sequence_changes(tmp_path, shape):
    path = tmp_path / "empty.db"
    history(path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM memory_vectors")
        db.execute("DELETE FROM memories")
        db.execute("DELETE FROM sqlite_sequence WHERE name='memories'")
        if shape == "absent":
            db.execute("DROP TABLE memory_vectors")
            db.execute("DROP TABLE memories")
        sequence = db.execute("SELECT * FROM sqlite_sequence ORDER BY name").fetchall()
    migrate(path, tmp_path / "backup.db")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM memories").fetchall() == []
        assert db.execute("SELECT * FROM sqlite_sequence ORDER BY name").fetchall() == sequence


def test_memory_sequence_need_not_be_last_and_keeps_deleted_high_watermark(tmp_path):
    path = tmp_path / "sequences.db"
    history(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sqlite_sequence SET seq=900 WHERE name='memories'")
        db.execute("INSERT INTO calls (conversation_id,connected_at,duration_s) VALUES ('c','2020-01-01T00:00:00+00:00',1)")
        before = db.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name").fetchall()
    migrate(path, tmp_path / "backup.db")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name").fetchall() == before
        appended = db.execute("INSERT INTO memories (user_id,work_id,source_character_id,text,created_at) VALUES ('u','sc','mano','new','2020-01-04T00:00:00+00:00')").lastrowid
        assert appended == 901


def test_extra_legacy_constraint_is_not_silently_discarded(tmp_path):
    path = tmp_path / "constraint.db"
    sql = (Path(__file__).parent / "fixtures/sc-memory-v1.sql").read_text()
    # Only the legacy memories text column has this exact declaration.
    sql = sql.replace("text TEXT NOT NULL,", "text TEXT NOT NULL CHECK(length(text)>0),")
    with sqlite3.connect(path) as db:
        db.executescript(sql)
        before = list(db.iterdump())
    with pytest.raises(RuntimeError, match="constraints"):
        migrate(path, tmp_path / "backup.db")
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
