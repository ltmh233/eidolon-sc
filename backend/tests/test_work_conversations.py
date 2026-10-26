"""Storage supports explicit work scopes; product APIs remain SC-only."""
import pytest
import sqlite3

from eidolon.data_access import Database
from eidolon.store import Store, NotFound


async def test_owner_and_work_bound_repository(tmp_path):
    from eidolon.work_conversations import WorkConversations
    db = Database(tmp_path / "new.db")
    try:
        store = Store(db)
        a, b = await store.create_user("A"), await store.create_user("B")
        # Trusted fixture registration, never a public client API.
        await db.write(lambda c: c.execute("INSERT INTO work_scopes VALUES ('demo')"))
        sc, demo, other = (WorkConversations(db, user_id=u, work_id=w)
                           for u, w in ((a.id, "sc"), (a.id, "demo"), (b.id, "sc")))
        one, two, three = await sc.create("mano"), await demo.create("mano"), await other.create("mano")
        await sc.append(one.id, "user", "SC")
        await demo.append(two.id, "user", "Demo")
        await other.append(three.id, "user", "B")
        assert [c.id for c in await sc.list_conversations("mano")] == [one.id]
        assert [c.id for c in await demo.list_conversations("mano")] == [two.id]
        for label, repository, foreign in (("SC -> Demo", sc, two), ("A -> B", sc, three), ("Demo -> SC", demo, one)):
            assert await repository.get(foreign.id) is None, (label, "get")
            for operation, action in (("append", lambda: repository.append(foreign.id, "user", "bad")),
                                      ("rename", lambda: repository.rename(foreign.id, "bad")),
                                      ("delete", lambda: repository.delete(foreign.id)),
                                      ("messages", lambda: repository.messages(foreign.id))):
                try:
                    await action()
                except NotFound:
                    pass
                else:
                    pytest.fail(f"{label}: {operation} did not reject foreign conversation")
        legacy_found = await store.get_conversation(one.id)
        assert legacy_found is not None and legacy_found.id == one.id
        assert await store.get_conversation(two.id) is None
        assert await store.list_messages(two.id) == []
        assert [c.id for c, *_ in await store.latest_conversations(a.id)] == [one.id]
        from eidolon.store import add_turn
        with pytest.raises(NotFound):
            await db.write(add_turn, two.id, "not SC", [])
        assert [m["text"] for m in await demo.messages(two.id)] == ["Demo"]
        with pytest.raises(sqlite3.IntegrityError):
            await WorkConversations(db, user_id=a.id, work_id="unregistered").create("mano")
        with pytest.raises(sqlite3.IntegrityError):
            await db.write(lambda c: c.execute("UPDATE conversations SET work_id='sc' WHERE id=?", (two.id,)))
        with pytest.raises(sqlite3.IntegrityError):
            await db.write(lambda c: c.execute("UPDATE messages SET conversation_id=? WHERE conversation_id=?", (one.id, two.id)))
        with pytest.raises(sqlite3.IntegrityError):
            await db.write(lambda c: c.execute("INSERT INTO calls (conversation_id,connected_at,duration_s) VALUES (?, 'date', 1)", (two.id,)))
        await sc.rename(one.id, "new")
        renamed = await sc.get(one.id)
        assert renamed is not None and renamed.title == "new"
        await demo.delete(two.id)
        assert await demo.get(two.id) is None
        assert [m["text"] for m in await sc.messages(one.id)] == ["SC"]
        assert [m["text"] for m in await other.messages(three.id)] == ["B"]
        with pytest.raises(sqlite3.IntegrityError, match="explicit registered work"):
            await db.write(lambda c: c.execute(
                "INSERT INTO conversations (id,user_id,character_id,created_at) VALUES ('unscoped',?,'mano',?)",
                (a.id, one.created_at),
            ))
        assert await sc.get("unscoped") is None
        assert await demo.get("unscoped") is None
    finally:
        await db.close()


@pytest.mark.parametrize("record", ["conversation", "message"])
async def test_replace_cannot_move_existing_records_across_owners_or_works(tmp_path, record):
    from eidolon.work_conversations import WorkConversations
    db = Database(tmp_path / "replace.db")
    try:
        store = Store(db)
        a, b = await store.create_user("A"), await store.create_user("B")
        await db.write(lambda c: c.execute("INSERT INTO work_scopes VALUES ('demo')"))
        sc = WorkConversations(db, user_id=a.id, work_id="sc")
        demo = WorkConversations(db, user_id=b.id, work_id="demo")
        one, two = await sc.create("mano"), await demo.create("mano")
        message = await sc.append(one.id, "user", "private")
        for label, sql, parameters in (
            ("conversation REPLACE", "INSERT OR REPLACE INTO conversations (id,user_id,work_id,character_id,created_at) VALUES (?,?,?,?,?)", (one.id, b.id, "demo", "mano", one.created_at)),
            ("message REPLACE", "INSERT OR REPLACE INTO messages (id,conversation_id,role,text,created_at) VALUES (?,?,?,?,?)", (message, two.id, "user", "private", one.created_at)),
        ):
            if label != f"{record} REPLACE":
                continue
            try:
                await db.write(lambda c: c.execute(sql, parameters))
            except sqlite3.IntegrityError:
                pass
            else:
                pytest.fail(f"{label}: reassigned existing identity")
        assert await demo.get(one.id) is None
        assert [m["text"] for m in await sc.messages(one.id)] == ["private"]
        assert await demo.messages(two.id) == []
    finally:
        await db.close()
