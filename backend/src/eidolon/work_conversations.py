"""Explicit owner/work-bound persistence; not a new product chat API.

No default work, registry mutation, engine, memory, affinity or gifts are exposed here.
All authorization predicates are checked within the same read/write transaction.
"""
import sqlite3
import uuid
from dataclasses import dataclass

from eidolon.data_access import Database
from eidolon.database import by_name, delete_conversation_data
from eidolon.store import NotFound, timestamp
from eidolon.work_schema import IDENTIFIER


@dataclass(frozen=True)
class WorkConversation:
    id: str
    user_id: str
    work_id: str
    character_id: str
    created_at: str
    title: str | None


class WorkConversations:
    def __init__(self, db: Database, *, user_id: str, work_id: str):
        if not user_id or not IDENTIFIER.fullmatch(work_id):
            raise ValueError("explicit owner and work required")
        self._db, self._user, self._work = db, user_id, work_id

    def _get(self, db: sqlite3.Connection, id: str) -> WorkConversation | None:
        row = by_name(db).execute(
            "SELECT * FROM conversations WHERE id=? AND user_id=? AND work_id=?",
            (id, self._user, self._work),
        ).fetchone()
        return WorkConversation(**row) if row else None

    def _require(self, db: sqlite3.Connection, id: str) -> WorkConversation:
        found = self._get(db, id)
        if found is None:
            raise NotFound("conversation outside owner/work scope")
        return found

    async def create(self, character_id: str) -> WorkConversation:
        if not IDENTIFIER.fullmatch(character_id):
            raise ValueError("local character ID required")
        def insert(db: sqlite3.Connection) -> WorkConversation:
            id = uuid.uuid4().hex
            db.execute("INSERT INTO conversations (id,user_id,work_id,character_id,created_at) VALUES (?,?,?,?,?)",
                       (id, self._user, self._work, character_id, timestamp()))
            return self._require(db, id)
        return await self._db.write(insert)

    async def get(self, id: str) -> WorkConversation | None:
        return await self._db.read(self._get, id)

    async def list_conversations(self, character_id: str) -> list[WorkConversation]:
        def read(db: sqlite3.Connection) -> list[WorkConversation]:
            rows = by_name(db).execute(
                "SELECT * FROM conversations WHERE user_id=? AND work_id=? AND character_id=? ORDER BY created_at DESC,rowid DESC",
                (self._user, self._work, character_id),
            )
            return [WorkConversation(**row) for row in rows]
        return await self._db.read(read)

    async def messages(self, id: str) -> list[dict]:
        def read(db: sqlite3.Connection) -> list[dict]:
            self._require(db, id)
            return [dict(row) for row in by_name(db).execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (id,))]
        return await self._db.read(read)

    async def append(self, id: str, role: str, text: str) -> int:
        def write(db: sqlite3.Connection) -> int:
            self._require(db, id)
            cursor = db.execute("INSERT INTO messages (conversation_id,role,text,created_at) VALUES (?,?,?,?)",
                                (id, role, text, timestamp()))
            assert cursor.lastrowid is not None
            return cursor.lastrowid
        return await self._db.write(write)

    async def rename(self, id: str, title: str) -> None:
        def write(db: sqlite3.Connection) -> None:
            self._require(db, id)
            db.execute("UPDATE conversations SET title=? WHERE id=? AND user_id=? AND work_id=?",
                       (title, id, self._user, self._work))
        await self._db.write(write)

    async def delete(self, id: str) -> None:
        def write(db: sqlite3.Connection) -> None:
            self._require(db, id)
            delete_conversation_data(db, id)
        await self._db.write(write)
