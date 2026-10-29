"""Private indexed SC summary candidates; no seen writes or title generation."""
import json

from fastapi import Depends, FastAPI, Request
from starlette.responses import StreamingResponse

from eidolon.affinity_auth import core_service_auth
from eidolon.assembly import Backend
from eidolon.sc_scope import sc_request
from eidolon.sessions import UserId
from eidolon.store import Conversation, LastCandidates, Message, ReachOut, comparison_order
from eidolon.display_text_contract import DISPLAY_HEADER, DISPLAY_CONTRACT, raw_display_fields


def add_summary_routes(app: FastAPI, backend: Backend) -> None:
    require_core = core_service_auth(backend.settings.core_service_token_path)
    dependencies = [Depends(require_core), Depends(sc_request)]

    @app.get('/api/_core/conversation-summaries', include_in_schema=False, dependencies=dependencies)
    async def listed(character_id: str, user_id: UserId, request: Request):
        raw_fields = raw_display_fields(request)
        await backend.titles.settle(user_id)
        rows = await backend.store.summary_candidates(user_id, character_id)
        return _response(backend, rows, 'list', user_id, raw_fields)

    @app.get('/api/_core/conversation-summaries/latest', include_in_schema=False, dependencies=dependencies)
    async def latest(user_id: UserId, request: Request):
        raw_fields = raw_display_fields(request)
        rows = await backend.store.summary_candidates(user_id, None)
        return _response(backend, rows, 'latest', None, raw_fields)


def _response(backend: Backend, rows: list[tuple[Conversation, LastCandidates, bool]], mode: str, user_id: str | None, raw_fields: bool = False):
    def encoded():
        yield json.dumps(['meta', 2, 'sc', mode, user_id, DISPLAY_CONTRACT] if raw_fields else ['meta', 1, 'sc', mode, user_id]) + '\n'
        for conversation, (message, event, reach_out, said), flag in rows:
            ordering_error = False

            def order_key(value: str) -> dict | None:
                nonlocal ordering_error
                try:
                    return comparison_order(value)
                except (ValueError, TypeError, OverflowError):
                    ordering_error = True
                    return None

            event_key = order_key(event.created_at) if event else None
            reach_key = order_key(reach_out.created_at) if reach_out else None
            turn_key = order_key(said if said is not None else message.created_at) if message and (event or reach_out) else None
            message_error = reach_error = False
            if raw_fields:
                m, r = message, reach_out
                message_error = _display_error(message) if message else False
                reach_error = _display_error(reach_out) if reach_out else False
            else:
                try:
                    m = message.without_cues() if message else None
                except (TypeError, AttributeError):
                    m, message_error = message, True
                try:
                    r = reach_out.without_cues() if reach_out else None
                except (TypeError, AttributeError):
                    r, reach_error = reach_out, True
            gift = backend.gift_catalog.get(event.gift_id) if event and event.gift_id is not None else None
            candidates = {
                'message': {'id': m.id, 'role': m.role, 'text': m.text, 'ja': m.ja if not message_error else None, 'zh': m.zh if not message_error else None,
                            'created_at': m.created_at, 'photo_status': m.photo.status if m.photo else None, 'display_error': message_error} if m else None,
                'event': {'id': event.id, 'kind': event.kind, 'created_at': event.created_at,
                          'gift_id': event.gift_id, 'gift_name': gift.name if gift else None, 'order_key': event_key} if event else None,
                'reach_out': {'id': r.id, 'ja': r.ja if not reach_error else '', 'zh': r.zh if not reach_error else '', 'created_at': r.created_at, 'display_error': reach_error, 'order_key': reach_key} if r else None,
                'last_turn_key': turn_key, 'ordering_error': ordering_error,
            }
            base = {'id': conversation.id, 'character_id': conversation.character_id,
                    'created_at': conversation.created_at, 'title': conversation.title}
            yield json.dumps(['row', base, flag, candidates], ensure_ascii=False, separators=(',', ':')) + '\n'
        yield '["end"]\n'

    headers = {'Cache-Control': 'no-store'}
    if raw_fields:
        headers[DISPLAY_HEADER] = DISPLAY_CONTRACT
    return StreamingResponse(encoded(), media_type='application/x-ndjson', headers=headers)


def _display_error(entry: Message | ReachOut) -> bool:
    """Keep legacy lazy TypeError/AttributeError without computing display text.

    Store still owns loading malformed JSON/segment shapes before this adapter.
    """
    if isinstance(entry, Message) and entry.role != 'character':
        return False
    nullable = isinstance(entry, Message)
    if any(not isinstance(value, str) and not (nullable and value is None) for value in (entry.ja, entry.zh)):
        return True
    try:
        return any(not isinstance(segment.ja, str) or not isinstance(segment.zh, str) for segment in entry.segments)
    except (TypeError, AttributeError):
        return True
