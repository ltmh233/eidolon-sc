"""One request owns the SC panel's ordered raw reads and existing state lifecycle.

The peer computes presentation only. No user identity or reward decision is accepted
from its control stream; the original writer remains the sole business writer.
"""
import asyncio
import contextlib
import json
import logging
import secrets
import sqlite3
import sys
import unicodedata

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from starlette.responses import Response

from eidolon import achievements, happenings
from eidolon.achievement_snapshot import UnsupportedContract, _decimal, _scalar, _stage
from eidolon.affinity import day_of, next_day
from eidolon.affinity_auth import core_service_auth, load_service_token
from eidolon.assembly import Backend
from eidolon.sc_scope import sc_request
from eidolon.sessions import UserId

log = logging.getLogger(__name__)
CONTRACT = 'sc-gifts-panel-v1'
_EOF = object()
_BASE_JSON = TypeAdapter(dict)


class ProtocolError(ValueError):
    """The peer did not finish this request's fixed conversation."""


class RejectedPanelResponse(Response):
    """Preserve an original handled error while finishing its duplex upload first."""

    def __init__(self, original: Response):
        super().__init__(original.body, status_code=original.status_code)
        self.raw_headers = list(original.raw_headers)

    async def __call__(self, scope, receive, send):
        deadline = asyncio.get_running_loop().time() + 1
        stopped = False
        reader = None

        async def drain():
            size = 0
            while not stopped:
                item = await receive()
                if stopped or item['type'] == 'http.disconnect':
                    return False
                size += len(item.get('body', b''))
                if size > 1024:
                    return False
                if not item.get('more_body', False):
                    return True
            return False

        try:
            # The status makes Go close its writer. Sending the JSON body only
            # after EOF prevents a close/reset race before that body is read.
            await send({'type': 'http.response.start', 'status': self.status_code, 'headers': self.raw_headers})
            reader = asyncio.create_task(drain())
            done, _ = await asyncio.wait({reader}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if done and reader.result():
                await asyncio.wait_for(send({'type': 'http.response.body', 'body': self.body, 'more_body': False}),
                                       max(0, deadline - asyncio.get_running_loop().time()))
        except (TimeoutError, OSError):
            pass
        finally:
            stopped = True
            if reader is not None:
                reader.cancel()
                done, _ = await asyncio.wait({reader}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
                if done and not reader.cancelled():
                    reader.exception()


class PanelRoute(APIRoute):
    """Only this GET's HTTPException responses need duplex transport completion."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def routed(request: Request) -> Response:
            try:
                return await handler(request)
            except HTTPException as exc:
                return RejectedPanelResponse(await http_exception_handler(request, exc))

        return routed


def _encode(row: object) -> bytes:
    return (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def _profile() -> list:
    if sys.version_info[:2] != (3, 11) or unicodedata.unidata_version != '14.0.0':
        raise UnsupportedContract()
    digits = sys.get_int_max_str_digits()
    if digits != 0 and digits < 640:
        raise UnsupportedContract()
    return ['py311-sql-v1', digits]


def _integer(value: object) -> str:
    if type(value) is not int:
        raise UnsupportedContract()
    return _decimal(value)


def _all_raw(db: sqlite3.Connection, user: str) -> tuple[list, list]:
    counts = db.execute('SELECT achievement,character_id,count FROM achievement_progress WHERE user_id=?', (user,)).fetchall()
    # Reuse only the old snapshot's SQL scalar/date normalization, not its projection.
    stages = [_stage(a, n, at) for a, n, at in db.execute(
        "SELECT achievement,stage,unlocked_at FROM achievement_stages WHERE user_id=? AND character_id=''", (user,))]
    return [['count', _scalar(a), _scalar(c), _scalar(n)] for a, c, n in counts], stages


async def _daily(backend: Backend, user: str, nonce: str) -> list:
    if not backend.happenings.open:
        return [['daily', 1, nonce, False, 0, None], ['ready', 1, nonce]]
    now = backend.happenings._clock()
    counts = await backend.database.read(happenings._counts, user, day_of(now))
    profile, tasks = _profile(), []
    if not isinstance(happenings.DAILY_TASKS, (tuple, list)):
        raise UnsupportedContract()
    for task in happenings.DAILY_TASKS:
        identifier, selector = getattr(task, 'id', None), getattr(task, 'counts', None)
        if not isinstance(identifier, str) or selector not in ('turns', 'gifts'):
            raise UnsupportedContract()
        tasks.append(['task', identifier, selector, _integer(getattr(task, 'goal', None)), _integer(getattr(task, 'draws', None))])
    return [['daily', 1, nonce, True, len(tasks), profile],
            ['daily-time', next_day(now).isoformat()], ['daily-counts', _scalar(counts.turns), _scalar(counts.gifts)],
            *tasks, ['ready', 1, nonce]]


async def _all(backend: Backend, user: str, nonce: str) -> list:
    if not backend.happenings.open:
        return [['all', 2, nonce, False, 0, 0, 0, None], ['ready', 2, nonce]]
    counts, stages = await backend.database.read(_all_raw, user)
    profile, definitions = _profile(), []
    tiers = achievements.TIERS_REACHED
    if not isinstance(tiers, (tuple, list)) or not all(isinstance(t, str) for t in tiers):
        raise UnsupportedContract()
    if not isinstance(achievements.ALL_CHARACTERS, (tuple, list)):
        raise UnsupportedContract()
    for rule in achievements.ALL_CHARACTERS:
        identifier, stages_rule = getattr(rule, 'id', None), getattr(rule, 'stages', None)
        if not isinstance(identifier, str) or not isinstance(stages_rule, (tuple, list)):
            raise UnsupportedContract()
        definitions.append(['achievement', identifier, [[_integer(getattr(s, 'goal', None)), _integer(getattr(s, 'draws', None))] for s in stages_rule]])
    return [['all', 2, nonce, True, len(definitions), len(counts), len(stages), profile],
            ['tiers', list(tiers)], *definitions, *counts, *stages, ['ready', 2, nonce]]


class PanelResponse(Response):
    """A single receive pump observes disconnects while one owner executes business work."""

    def __init__(self, backend: Backend, user: str, release):
        super().__init__(None, media_type='application/x-ndjson',
                         headers={'Cache-Control': 'no-store', 'Connection': 'close'})
        self.raw_headers = [(k, v) for k, v in self.raw_headers if k != b'content-length']
        self.backend, self.user, self.release = backend, user, release

    async def __call__(self, scope, receive, send):
        owner = asyncio.current_task()
        assert owner is not None
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        closed, eof, protocol, internal = False, False, False, False
        phase, state_started = 0, False
        terminal, state_ready, error_sent = False, False, False
        nonce = ''
        deadline = asyncio.get_running_loop().time() + 60
        input_ended = asyncio.get_running_loop().create_future()

        def ended(at_eof=False):
            if not input_ended.done():
                input_ended.set_result(at_eof)

        async def emit_bytes(body):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError()
            await asyncio.wait_for(send({'type': 'http.response.body', 'body': body, 'more_body': True}), remaining)

        async def emit(row):
            await emit_bytes(_encode(row))

        async def pump():
            nonlocal closed, eof, protocol, internal
            buffer, total, lines, last = b'', 0, 0, None
            try:
                while not closed:
                    item = await receive()
                    if closed:
                        return
                    if item['type'] == 'http.disconnect':
                        ended()
                        closed = True
                        if not terminal:
                            owner.cancel()
                        return
                    if not item.get('more_body', False):
                        ended(True)
                    body = item.get('body', b'')
                    total += len(body)
                    if eof or total > 1024:
                        raise ProtocolError()
                    buffer += body
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        lines += 1
                        if len(line) + 1 > 256 or lines > 4:
                            raise ProtocolError()
                        text = line.decode('utf-8', errors='strict')
                        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError()))
                        # All control strings are ASCII, so unpaired Unicode is rejected by shape.
                        if not isinstance(value, list):
                            raise ProtocolError()
                        last = value
                        queue.put_nowait(value)
                    if len(buffer) >= 256:
                        raise ProtocolError()
                    if not item.get('more_body', False):
                        if buffer:
                            raise ProtocolError()
                        eof = True
                        ended(True)
                        if terminal:
                            continue
                        queue.put_nowait(_EOF)
                        valid_abort = (phase in (1, 2) and isinstance(last, list) and last == ['abort', phase, nonce, 'legacy']
                                       and type(last[1]) is int)
                        valid_finish = (state_ready and isinstance(last, list) and len(last) == 4
                                        and last[:3] == ['finish', 3, nonce] and type(last[1]) is int
                                        and last[3] in ('ok', 'legacy'))
                        if not valid_abort and not valid_finish:
                            protocol, closed = True, True
                            owner.cancel()
                        # There is no more business after a valid abort/finish. A second
                        # receive here can race BaseHTTPMiddleware's cancellation scope
                        # while the terminal response is being flushed.
                        return
            except (ValueError, UnicodeError, asyncio.QueueFull):
                if closed:
                    return
                protocol, closed = True, True
                if not terminal:
                    owner.cancel()
                # The control parser is closed permanently. The same receive owner
                # may only discard transport bytes until EOF/disconnect so closing
                # an unread upload cannot reset the terminal response.
                try:
                    while not input_ended.done():
                        item = await receive()
                        if item['type'] == 'http.disconnect' or not item.get('more_body', False):
                            ended(item['type'] == 'http.request')
                except Exception:
                    ended()
            except Exception:
                ended()
                if closed:
                    return
                internal, closed = True, True
                log.exception('SC gifts panel receive failed')
                if not terminal:
                    owner.cancel()

        async def control():
            return await asyncio.wait_for(queue.get(), min(10, max(0, deadline - asyncio.get_running_loop().time())))

        async def finish_eof():
            if await control() is not _EOF:
                raise ProtocolError()

        async def advance(number, nonce):
            value = await control()
            if value == ['abort', number, nonce, 'legacy'] and type(value[1]) is int:
                await finish_eof()
                await emit(['end'])
                return False
            if value != ['next', number, nonce] or type(value[1]) is not int or closed or eof:
                raise ProtocolError()
            return True

        reader = None
        try:
            await send({'type': 'http.response.start', 'status': 200, 'headers': self.raw_headers})
            reader = asyncio.create_task(pump())
            async with asyncio.timeout_at(deadline):
                if await control() != ['begin', CONTRACT] or closed or eof:
                    raise ProtocolError()
                await emit(['hello', CONTRACT, 'sc', 'http1-duplex-v1'])
                for number, read in ((1, _daily), (2, _all)):
                    phase = number
                    nonce = secrets.token_hex(16)
                    rows = await read(self.backend, self.user, nonce)
                    # Validate the entire phase before its success header, without holding SQL.
                    for row in rows:
                        _encode(row)
                    for row in rows:
                        await emit(row)
                    if not await advance(number, nonce):
                        return
                # Linearization: no await between checking liveness and authorizing state.
                if closed or eof or state_started or asyncio.get_running_loop().time() >= deadline:
                    raise ProtocolError()
                state_started, phase = True, 3
                base = (await self.backend.gifts.state(self.user)).shown()
                # The original annotated dict response uses Pydantic dump_json, which
                # preserves its non-finite->null and UTF-8 bytes->string behavior.
                encoded_base = _BASE_JSON.dump_json(_BASE_JSON.validate_python(base))
                nonce = secrets.token_hex(16)
                state_ready = True
                await emit_bytes(b'["state",3,' + json.dumps(nonce).encode() + b',' + encoded_base + b']\n')
                value = await control()
                if (not isinstance(value, list) or len(value) != 4 or value[:3] != ['finish', 3, nonce]
                        or type(value[1]) is not int or value[3] not in ('ok', 'legacy')):
                    raise ProtocolError()
                await finish_eof()
                await emit(['end'])
        except asyncio.CancelledError:
            terminal = True
            if protocol or internal:
                with contextlib.suppress(TimeoutError, OSError):
                    await emit(['error', phase, 'protocol' if protocol else 'internal'])
                    error_sent = True
            elif not closed:
                raise
        except Exception as exc:
            terminal = True
            if isinstance(exc, UnsupportedContract):
                code = 'unsupported'
            elif isinstance(exc, ProtocolError):
                code = 'protocol'
            elif isinstance(exc, TimeoutError):
                code = 'timeout'
            elif isinstance(exc, (sqlite3.Error, TypeError, ValueError, OverflowError, IndexError)) and phase:
                code = 'legacy'
            else:
                code = 'internal'
                log.exception('SC gifts panel dialog failed')
            with contextlib.suppress(TimeoutError, OSError):
                await emit(['error', phase, code])
                error_sent = True
        finally:
            cleanup_deadline = asyncio.get_running_loop().time() + 1
            try:
                drained = not terminal
                if terminal:
                    # A conforming peer closes its control writer on error. Let
                    # that EOF arrive before Connection: close can reset an upload
                    # still in flight. This is transport cleanup only: no business
                    # phase can resume, and it shares the existing one-second budget.
                    if reader is not None and not reader.done():
                        await asyncio.wait({input_ended}, timeout=max(0, cleanup_deadline - asyncio.get_running_loop().time()))
                    drained = input_ended.done() and input_ended.result()
                closed = True
                if terminal:
                    completed_end = False
                    if drained and error_sent:
                        with contextlib.suppress(TimeoutError, OSError, RuntimeError):
                            await asyncio.wait_for(send({'type': 'http.response.body', 'body': _encode(['end']), 'more_body': True}),
                                                   max(0, min(deadline, cleanup_deadline) - asyncio.get_running_loop().time()))
                            completed_end = True
                    drained = completed_end
                # BaseHTTPMiddleware's receive races response_sent. Complete the
                # response first so a swallowed receive cancellation cannot make
                # pump shutdown depend on a final body the owner has not sent yet.
                if drained:
                    with contextlib.suppress(TimeoutError, OSError, RuntimeError):
                        await asyncio.wait_for(send({'type': 'http.response.body', 'body': b'', 'more_body': False}), max(0, cleanup_deadline - asyncio.get_running_loop().time()))
                if reader is not None:
                    reader.cancel()
                    completed, _ = await asyncio.wait({reader}, timeout=max(0, cleanup_deadline - asyncio.get_running_loop().time()))
                    if not completed:
                        reader.cancel()
                        log.error('SC gifts panel receive cleanup exceeded one second')
                    elif not reader.cancelled():
                        try:
                            reader.result()
                        except Exception:
                            log.exception('SC gifts panel receive cleanup failed')
            finally:
                self.release()


def add_gifts_panel(app: FastAPI, backend: Backend) -> None:
    path = backend.settings.core_gifts_panel_token_path
    token = load_service_token(path)
    if token is not None:
        for other in (backend.settings.core_service_token_path, backend.settings.core_sc_create_token_path):
            value = load_service_token(other)
            if value is not None and secrets.compare_digest(value, token):
                raise RuntimeError('SC gifts panel, read and create credentials must be distinct')
    active = 0

    def release():
        nonlocal active
        active -= 1

    routes = APIRouter(route_class=PanelRoute)

    @routes.get('/api/_core/sc-gifts-panel', include_in_schema=False,
                dependencies=[Depends(core_service_auth(path)), Depends(sc_request)])
    async def panel(request: Request, user: UserId):
        nonlocal active
        if active >= 8:
            raise HTTPException(503, 'SC gifts panel unavailable')
        if request.scope.get('http_version') != '1.1':
            raise HTTPException(503, 'SC gifts panel requires HTTP/1.1')
        active += 1
        return PanelResponse(backend, user, release)

    app.include_router(routes)
