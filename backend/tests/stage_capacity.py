"""Real public HTTP load plus controlled private duplex peers; synthetic data only.

Run inside the dedicated 512MiB load container after functional acceptance.
Every result is flushed before proceeding; an assertion stops further concurrency.
"""
import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from urllib.parse import urlsplit

import httpx

from tests.test_gifts_panel_protocol import Wire
from tests.test_sc_play_scope import gameplay_snapshot
from tests.stage_acceptance import SafetyStop, assert_admitted, assert_no_oom


async def wire_probe(directory, client, endpoints):
    peer = urlsplit(endpoints['python'])
    before = gameplay_snapshot(directory / 'test.db')
    wire = await Wire.open(peer.port, client, host=peer.hostname, token=(directory / 'panel-token').read_text())
    try:
        assert wire.status == 200
        added = {'records': 0, 'utf8_bytes': 0}

        def observe(row, size):
            if row[0] == 'count' and row[2][0] == 's' and row[2][1].startswith('retired_'):
                added['records'] += 1
                added['utf8_bytes'] += size

        wire.on_record = observe
        rows = await wire.next_all()
        count_rows = [row for row in rows if row[0] == 'count']
        await wire.write(['abort', 2, rows[0][2], 'legacy'])
        await wire.end()
        from tests.stage_acceptance import SIZES
        assert added == {'records': SIZES[endpoints['size']], 'utf8_bytes': {'S': 5650, 'M': 565000, 'L': 5650000}[endpoints['size']]}, added
        result = {'count_records': len(count_rows), 'new_candidates_actual_wire': added, 'record_sizes': wire.record_sizes,
                  'response_decoded_bytes': sum(value[1] for value in wire.record_sizes.values()),
                  'early_abort_sql_unchanged': gameplay_snapshot(directory / 'test.db') == before}
        assert result['early_abort_sql_unchanged']
        (directory / 'private-wire.json').write_text(json.dumps(result, indent=2))
        return result
    finally:
        await wire.close()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()


async def wait_for(predicate, seconds=2):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(.01)


def runtime(directory):
    try:
        return json.loads((directory / 'runtime.json').read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def run(directory):
    assert_admitted(directory)
    endpoints = json.loads((directory / 'endpoints.json').read_text())
    cookie = json.loads((directory / 'session-cookies.json').read_text())
    results = []

    def record(value):
        value['at_unix'] = time.time()
        results.append(value)
        with (directory / 'capacity.jsonl').open('a') as file:
            file.write(json.dumps(value, ensure_ascii=False) + '\n')
        print(json.dumps(value, ensure_ascii=False), flush=True)

    async with httpx.AsyncClient(base_url=endpoints['next'], cookies=cookie, trust_env=False,
                                 headers={'Origin': 'http://localhost'}, timeout=70) as client:
        panel = await client.get('/api/gifts')
        assert_no_oom(directory)
        direct = await client.get(endpoints['python'] + '/api/gifts')
        assert_no_oom(directory)
        assert panel.status_code == direct.status_code == 200
        assert panel.json() == direct.json()
        expected = panel.json()
        record({'scenario': 'actual-private-wire', **await wire_probe(directory, client, endpoints)})
        assert_no_oom(directory)
        (directory / 'capacity-panel-oracle.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2))
        before = gameplay_snapshot(directory / 'test.db')
        (directory / 'capacity-sql-before.json').write_text(json.dumps(before, ensure_ascii=False, default=str))
        record({'scenario': 'oracle', 'status': 200, 'public_bytes': len(panel.content),
                'body_sha256': digest(expected), 'sql_sha256': digest(before),
                'seeded_sql': json.loads((directory / 'seeded-sql.json').read_text())})

        async def one_panel():
            assert_no_oom(directory)
            started = time.monotonic()
            response = await client.get('/api/gifts')
            assert_no_oom(directory)
            elapsed = time.monotonic() - started
            return {'status': response.status_code, 'seconds': elapsed,
                    'matches_oracle': response.status_code == 200 and response.json() == expected}

        for concurrency in (1, 4, 8):
            for _ in range(5):
                assert_no_oom(directory)
                warmup = await asyncio.gather(*(one_panel() for _ in range(concurrency)))
                assert_no_oom(directory)
                assert all(row['matches_oracle'] for row in warmup), warmup
            measured = []
            start = time.monotonic()
            measured_start_unix = time.time()
            for _ in range(20):
                assert_no_oom(directory)
                batch = await asyncio.gather(*(one_panel() for _ in range(concurrency)))
                assert_no_oom(directory)
                measured.extend(batch)
                if not all(row['matches_oracle'] for row in batch):
                    record({'scenario': 'panel', 'concurrency': concurrency, 'failed_batch': batch,
                            'classification': 'infrastructure502-pending-cgroup-and-upstream' if any(row['status'] == 502 for row in batch) else 'response-mismatch-or-HTTP-error'})
                    raise AssertionError('failed stable panel batch; higher concurrency safety-stopped')
                state = runtime(directory)
                assert state.get('writer_queued', 0) < 8, state
            seconds = sorted(row['seconds'] for row in measured)
            record({'scenario': 'panel', 'concurrency': concurrency, 'warmup_batches': 5,
                    'measured_batches': 20, 'completed': len(measured), 'overload': 0, 'business_errors': 0,
                    'min_s': seconds[0], 'p50_s': statistics.median(seconds),
                    'p95_s': seconds[int(len(seconds) * .95) - 1], 'max_s': seconds[-1],
                    'wall_s': time.monotonic() - start, 'measured_start_unix': measured_start_unix,
                    'runtime': runtime(directory)})

        # Same payloads, independently exercise total history and largest single record.
        for conversation in ('stage-many-small', 'stage-one-large'):
            path = f'/api/conversations/{conversation}/messages'
            original = await client.get(endpoints['python'] + path)
            assert_no_oom(directory)
            assert original.status_code == 200
            oracle = original.json()
            encoded_bytes = []
            measured_start_unix = None
            for iteration in range(25):
                assert_no_oom(directory)
                if iteration == 5:
                    measured_start_unix = time.time()
                started = time.monotonic()
                response = await client.get(path)
                assert_no_oom(directory)
                assert response.status_code == 200 and response.json() == oracle
                if iteration >= 5:
                    encoded_bytes.append((len(response.content), time.monotonic() - started))
            record({'scenario': 'history', 'conversation': conversation, 'warmup': 5, 'completed': 20,
                    'public_bytes': encoded_bytes[0][0], 'max_s': max(x[1] for x in encoded_bytes),
                    'measured_start_unix': measured_start_unix,
                    'body_sha256': digest(oracle)})

        # Hold two real model streams, with distinct conversations, while panel and plain HTTP proceed.
        conversations = []
        for character in ('mano', 'kogane'):
            response = await client.post('/api/conversations', json={'character_id': character})
            assert_no_oom(directory)
            assert response.status_code == 201, response.text
            conversations.append(response.json()['id'])
        cancellation_times = []
        mixed_start_unix = None
        for iteration in range(25):
            assert_no_oom(directory)
            if iteration == 5:
                mixed_start_unix = time.time()
            markers = [f'cancel-stream:stage-{iteration}-{index}' for index in range(2)]
            async with AsyncExitStack() as stack:
                async def open_stream(index):
                    response = await stack.enter_async_context(client.stream('POST', f'/api/conversations/{conversations[index]}/turns', json={'text': markers[index]}))
                    assert response.status_code == 200, await response.aread()
                    lines = response.aiter_lines()
                    async for line in lines:
                        if line.startswith('data: ') and json.loads(line[6:]).get('type') == 'delta' and json.loads(line[6:]).get('text'):
                            # Retain the live iterator as well as the response. Otherwise
                            # generator finalization closes the socket before mixed load.
                            return response, lines
                    raise AssertionError('model SSE never reached delta')
                streams = await asyncio.gather(*(open_stream(i) for i in range(2)))
                await wait_for(lambda: runtime(directory).get('active_models') == 2)
                batch, plain = await asyncio.gather(asyncio.gather(*(one_panel() for _ in range(4))), client.get('/api/works'))
                assert_no_oom(directory)
                assert plain.status_code == 200 and all(row['matches_oracle'] for row in batch), batch
                disconnected = time.monotonic()
                assert len(streams) == 2
            def canceled():
                events = [json.loads(line) for line in (directory / 'model-events.jsonl').read_text().splitlines()]
                return all(any(event['kind'] == 'cancel' and event['key'] == marker for event in events) for marker in markers)
            await wait_for(canceled)
            if iteration >= 5:
                cancellation_times.append(time.monotonic() - disconnected)
        await wait_for(lambda: runtime(directory).get('active_models') == 0)
        record({'scenario': 'mixed-two-SSE', 'warmup_cycles': 5, 'measured_cycles': 20,
                'model_cancels': 40, 'panel_completed': 80, 'plain_completed': 20,
                'measured_start_unix': mixed_start_unix,
                'max_observed_cancel_s': max(cancellation_times), 'runtime_after': runtime(directory)})

        # Saturate actual private slots. The ninth private503 maps to public502; no state is authorized.
        peer = urlsplit(endpoints['python'])
        token = (directory / 'panel-token').read_text()
        before_slots = gameplay_snapshot(directory / 'test.db')
        for iteration in range(2):
            assert_no_oom(directory)
            wires = await asyncio.gather(*(Wire.open(peer.port, client, host=peer.hostname, token=token) for _ in range(8)))
            try:
                assert all(wire.status == 200 for wire in wires)
                ninth = await Wire.open(peer.port, client, host=peer.hostname, token=token)
                assert ninth.status == 503
                await ninth.eof()
                await asyncio.wait_for(ninth.reader.read(), 2)
                await ninth.close()
                response = await client.get('/api/gifts')
                assert response.status_code == 502, response.text
            finally:
                await asyncio.gather(*(wire.close() for wire in wires))
            await asyncio.sleep(.15)
        assert gameplay_snapshot(directory / 'test.db') == before_slots
        assert (await client.get('/api/gifts')).json() == expected
        record({'scenario': 'slots', 'occupy_release_reoccupy': [8, 8], 'private_overload': [503, 503],
                'public_overload': [502, 502], 'unauthorized_state_sql_unchanged': True})

        after = gameplay_snapshot(directory / 'test.db')
        (directory / 'capacity-sql-after.json').write_text(json.dumps(after, ensure_ascii=False, default=str))
        # Streams deliberately append user messages/conversations. Economy reads remain idempotent.
        for table in before:
            if table not in ('conversations', 'messages', 'achievement_progress', 'daily_task_progress', 'affinities'):
                assert before[table] == after[table], table
        await asyncio.sleep(.5)
        state = runtime(directory)
        assert_no_oom(directory)
        assert state['active_models'] == state['writer_queued'] == state['readers_running'] == state['readers_waiting'] == 0, state
        record({'scenario': 'released', 'runtime': state, 'sql_sha256': digest(after)})


if __name__ == '__main__':
    output = Path(sys.argv[1])
    if len(sys.argv) == 3 and sys.argv[2] == 'wire-probe':
        async def probe():
            endpoints = json.loads((output / 'endpoints.json').read_text())
            async with httpx.AsyncClient(cookies=json.loads((output / 'session-cookies.json').read_text()), trust_env=False) as client:
                print(json.dumps(await wire_probe(output, client, endpoints)))
        asyncio.run(probe())
        sys.exit(0)
    assert not (output / 'capacity.jsonl').exists(), 'fresh capacity output required'
    try:
        asyncio.run(run(output))
    except BaseException as error:
        (output / 'capacity.exit').write_text('1\n')
        with (output / 'capacity.jsonl').open('a') as file:
            file.write(json.dumps({'scenario': 'aborted', 'error': repr(error),
                                   'classification': 'safety-stop' if isinstance(error, SafetyStop) else 'request-or-harness-error',
                                   'safety_evidence': ('actual-OS-OOM' if (output / 'oom-stop').exists() else
                                                       'operator-interrupt' if (output / 'operator-interrupt').exists() else
                                                       'observer-or-functional' if isinstance(error, SafetyStop) else None),
                                   'higher_concurrency': 'safety-stopped'}) + '\n')
        raise
    (output / 'capacity.exit').write_text('0\n')
