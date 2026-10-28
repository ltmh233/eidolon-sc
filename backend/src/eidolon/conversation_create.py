"""SC creation protocol; SQLite writes remain on Python's single writer."""
import json
from dataclasses import asdict
import secrets
import sqlite3
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from eidolon.affinity_auth import core_service_auth, load_service_token
from eidolon.assembly import Backend
from eidolon.sessions import UserId

from eidolon.sc_scope import require_sc, sc_request


class NewConversation(BaseModel):
    character_id: str


async def sc_creation_request(request: Request) -> None:
    """Inspect only creation's scope declarations, preserving duplicate JSON keys.

    FastAPI keeps its original model/errors for malformed JSON and other fields.
    The cached request bytes are shared with its normal body decoder.
    """
    sc_request(request)
    try:
        pairs = json.loads(await request.body(), object_pairs_hook=lambda p: p)
    except (ValueError, UnicodeDecodeError):
        return

    def declarations(value: object) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == 'work_id':
                require_sc(item[1])

    declarations(pairs)
    if isinstance(pairs, list):
        for item in pairs:
            if isinstance(item, tuple) and len(item) == 2 and item[0] == 'requestData':
                declarations(item[1])


class CreateSCConversation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: Literal['CreateSCConversation']
    version: int = Field(ge=1, le=1)
    work_id: Literal['sc']
    user_id: str
    character_id: str
    content_digest: str
    command_id: str = Field(pattern=r'^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$')


def add_create_routes(app: FastAPI, backend: Backend) -> None:
    path = backend.settings.core_sc_create_token_path
    write = load_service_token(path)
    read = load_service_token(backend.settings.core_service_token_path)
    if write is not None:
        if read is not None and secrets.compare_digest(write, read):
            raise RuntimeError('SC create and read credentials must be distinct')
        if backend.sc_content_digest is None:
            raise RuntimeError('SC creation requires a loaded v3 content snapshot')
    dependencies = [Depends(core_service_auth(path)), Depends(sc_creation_request)]

    @app.post('/api/_core/sc-conversation-create/prepare', include_in_schema=False, dependencies=dependencies)
    async def prepare(body: NewConversation, user_id: UserId, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        # Go owns membership admission; this endpoint only provides authenticated input.
        return {'version': 1, 'work_id': 'sc', 'user_id': user_id,
                'character_id': body.character_id, 'content_digest': backend.sc_content_digest}

    @app.post('/api/_core/sc-conversation-create/execute', status_code=201, include_in_schema=False, dependencies=dependencies)
    async def execute(body: CreateSCConversation, user_id: UserId, response: Response):
        if body.user_id != user_id or body.content_digest != backend.sc_content_digest or body.character_id not in backend.characters:
            raise HTTPException(409, 'SC conversation command rejected')
        try:
            conversation = await backend.store.create_conversation(user_id, body.character_id, conversation_id=body.command_id)
        except sqlite3.IntegrityError:
            # Includes ID collisions and account deletion races; disclose no row data.
            raise HTTPException(409, 'SC conversation command rejected') from None
        response.headers['Cache-Control'] = 'no-store'
        return asdict(conversation)
