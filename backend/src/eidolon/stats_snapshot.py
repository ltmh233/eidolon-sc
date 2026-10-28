"""Private statistics metadata, never a public user-data endpoint or a reward writer."""
import json
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query
from starlette.responses import StreamingResponse

from eidolon.affinity_auth import core_service_auth
from eidolon.assembly import Backend
from eidolon.characters import in_idol_order
from eidolon.producer import effective_timezone
from eidolon.sc_scope import sc_request
from eidolon.sessions import UserId
from eidolon.stats import Range


def add_stats_snapshot(app: FastAPI, backend: Backend) -> None:
    require_core = core_service_auth(backend.settings.core_service_token_path)

    @app.get('/api/_core/stats', include_in_schema=False,
             dependencies=[Depends(require_core), Depends(sc_request)])
    async def snapshot(
        user_id: UserId,
        span: Annotated[Range, Query(alias='range')] = 'all',
        device_timezone: Annotated[str | None, Header(alias='X-Timezone')] = None,
    ):
        store = backend.store
        zone = ZoneInfo(effective_timezone(await store.get_profile(user_id), device_timezone))
        signed_up = datetime.fromisoformat((await store.get_user(user_id)).created_at)
        contacts = await store.contacts(user_id)
        first = await store.first_records(user_id)
        calls = await store.kept_calls(user_id)
        idols = in_idol_order(backend.characters.values())
        enabled = backend.config.affinity_on
        states = await backend.affinities.sc_stored_states(user_id, [c.id for c in idols]) if enabled else {}
        tally = await backend.happenings.tally(user_id)  # Existing read-only achievement result.
        observed = backend.clock().astimezone(zone).isoformat()
        indices = {c.id: i for i, c in enumerate(idols)}
        next_unknown = -1

        def index(character: str) -> int:
            nonlocal next_unknown
            if character not in indices:
                # Preserve distinct removed/imported characters for gift maxima, without IDs.
                indices[character] = next_unknown
                next_unknown -= 1
            return indices[character]

        def at(when: datetime) -> str:
            return when.astimezone(zone).isoformat()

        def rows():
            yield ['meta', 1, 'sc', span, observed, at(signed_up), enabled, tally.shown() if tally else None]
            for c in idols:
                yield ['character', c.id, states.get(c.id)]
            for character, when in first.items():
                yield ['first', index(character), at(when)]
            for contact in contacts:
                yield ['contact', index(contact.character_id), contact.kind, at(contact.at), contact.liked]
            for call in calls:
                yield ['call', index(call.character_id), at(call.connected_at), call.duration_s]
            yield ['end']

        def encoded():
            batch = bytearray()
            for row in rows():
                batch.extend((json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode())
                if len(batch) >= 32 * 1024:
                    yield bytes(batch)
                    batch.clear()
            if batch:
                yield bytes(batch)

        return StreamingResponse(encoded(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-store'})
