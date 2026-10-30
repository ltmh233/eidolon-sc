"""One S bridge instance: actual private identity/closing/queued-writer evidence."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from tests.stage_capacity import wait_for
from tests.test_gifts_panel_protocol import Wire
from tests.test_sc_play_scope import gameplay_snapshot


async def run(directory):
    info = json.loads((directory / 'backend.json').read_text())
    endpoints = json.loads((directory / 'endpoints.json').read_text())
    assert endpoints['topology'] == 'bridge' and endpoints['size'] == 'S' and info['fault_code']
    peer = urlsplit(endpoints['python'])
    token = (directory / 'panel-token').read_text()

    def record(value):
        with (directory / 'faults.jsonl').open('a') as file:
            file.write(json.dumps(value) + '\n')
        print(json.dumps(value), flush=True)

    async def rejected(client, status):
        direct = await client.get(endpoints['python'] + '/api/gifts')
        public = await client.get(endpoints['next'] + '/api/gifts')
        assert direct.status_code == public.status_code == status
        assert direct.json() == public.json()
        before = gameplay_snapshot(directory / 'test.db')
        wire = await Wire.open(peer.port, client, host=peer.hostname, token=token)
        try:
            assert wire.status == status
            await wire.eof()
            raw = await asyncio.wait_for(wire.reader.read(), 2)
            assert json.dumps(direct.json(), ensure_ascii=False, separators=(',', ':')).encode() in raw
            assert gameplay_snapshot(directory / 'test.db') == before
            record({'scenario': 'bridge-identity', 'private_status': status, 'public_status': status,
                    'complete_body_matches_direct': True, 'zero_gameplay_writes': True, 'actual_eof': True})
        finally:
            await wire.close()

    async with httpx.AsyncClient(trust_env=False, headers={'Origin': 'http://localhost'}) as client:
        await rejected(client, 401)
        start = await client.get(endpoints['next'] + '/api/auth/github/start')
        state = parse_qs(urlsplit(start.headers['location']).query)['state'][0]
        callback = await client.get(endpoints['next'] + '/api/auth/github/callback', params={'state': state, 'code': info['fault_code']})
        assert callback.status_code == 302
        client.cookies = dict(client.cookies)  # synthetic cross-DNS oracle only
        await rejected(client, 428)
        consent = await client.post(endpoints['next'] + '/api/account/consent', json={'agree_to_terms': True, 'at_least_13': True})
        assert consent.status_code == 204
        (directory / 'auth-ban-arm').touch()
        await wait_for(lambda: (directory / 'auth-ban-ready').exists())
        await rejected(client, 403)
        (directory / 'auth-unban-arm').touch()
        await wait_for(lambda: (directory / 'auth-unban-ready').exists())

        before_errors = gameplay_snapshot(directory / 'test.db')
        for with_eof in (True, False):
            wire = await Wire.open(peer.port, client, host=peer.hostname, token=token)
            try:
                assert wire.status == 200
                await wire.raw(b'\n')
                error = await wire.row()
                assert error[0] == 'error' and error[2] == 'protocol', error
                started = time.monotonic()
                if with_eof:
                    await wire.end()
                    outcome = 'strict-end-and-EOF'
                else:
                    remainder = await asyncio.wait_for(wire.reader.read(), 2)
                    assert b'["end"]' not in wire.buffer + remainder
                    assert time.monotonic() - started <= 1.5
                    outcome = 'bounded-close-without-end'
                assert gameplay_snapshot(directory / 'test.db') == before_errors
                record({'scenario': 'bridge-error-closing', 'request_eof': with_eof,
                        'actual_error': error, 'outcome': outcome, 'seconds': time.monotonic() - started,
                        'zero_state': True})
            finally:
                await wire.close()
        recovered = await asyncio.gather(*(Wire.open(peer.port, client, host=peer.hostname, token=token) for _ in range(8)))
        assert all(wire.status == 200 for wire in recovered)
        await asyncio.gather(*(wire.close() for wire in recovered))
        await asyncio.sleep(.15)
        record({'scenario': 'bridge-error-slots-recovered', 'actual_slots': 8})

        # A genuinely new user is created AFTER the baseline's all-user reset-card grant.
        # The earlier fault user legitimately already has a balance from that R2 check.
        client.cookies.clear()
        start = await client.get(endpoints['next'] + '/api/auth/github/start')
        state = parse_qs(urlsplit(start.headers['location']).query)['state'][0]
        callback = await client.get(endpoints['next'] + '/api/auth/github/callback', params={'state': state, 'code': info['writer_code']})
        assert callback.status_code == 302
        client.cookies = dict(client.cookies)
        assert (await client.post(endpoints['next'] + '/api/account/consent', json={'agree_to_terms': True, 'at_least_13': True})).status_code == 204
        account = await client.get(endpoints['next'] + '/api/account')
        assert account.status_code == 200
        writer_user = account.json()['id']

        def balances():
            with sqlite3.connect(directory / 'test.db') as db:
                return db.execute('SELECT draws FROM gift_balances WHERE user_id=?', (writer_user,)).fetchall()

        assert balances() == []
        (directory / 'writer-block-arm').touch()
        await wait_for(lambda: (directory / 'writer-block-entered').exists())
        observed_before = [json.loads(line) for line in (directory / 'requests.jsonl').read_text().splitlines()]
        private_before = sum(q['path'] == '/api/_core/sc-gifts-panel' for q in observed_before)
        request = asyncio.create_task(client.get(endpoints['next'] + '/api/gifts'))
        try:
            await wait_for(lambda: (directory / 'writer-state-queued').exists())
            assert balances() == []
            disconnected = time.monotonic()
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            await wait_for(lambda: (directory / 'writer-owner-cancelled').exists())
            # Required observation BEFORE the test gate is released: real state is queued,
            # owner already canceled, and the transaction has not yet run.
            assert not (directory / 'writer-release').exists() and balances() == []
            owner_cancelled = float((directory / 'writer-owner-cancelled').read_text())
            record({'scenario': 'bridge-queued-disconnect-before-release', 'state_actually_queued': True,
                    'owner_cancelled_before_gate_release': True, 'balance_rows_before_release': 0,
                    'cancel_s': owner_cancelled - disconnected})
            (directory / 'writer-release').touch()
            await wait_for(lambda: (directory / 'writer-drained').exists())
            assert balances() == [(1,)]
            observed = [json.loads(line) for line in (directory / 'requests.jsonl').read_text().splitlines()]
            private_delta = sum(q['path'] == '/api/_core/sc-gifts-panel' for q in observed) - private_before
            assert private_delta == 1
            record({'scenario': 'bridge-queued-original-commit', 'balance_draws': balances(),
                    'actual_private_request_delta': private_delta,
                    'public_client_cancelled': request.cancelled(), 'original_transaction_committed': True})
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)


if __name__ == '__main__':
    output = Path(sys.argv[1])
    assert not (output / 'faults.jsonl').exists()
    try:
        asyncio.run(run(output))
    except BaseException:
        (output / 'faults.exit').write_text('1\n')
        raise
    (output / 'faults.exit').write_text('0\n')
