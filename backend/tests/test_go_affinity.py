"""Fixed-clock differential contract: Go calculates, Python owns auth and SQLite."""
import asyncio
from datetime import datetime, UTC, timedelta, timezone
import os
from pathlib import Path
import random
import socket

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from typing import cast

from tests.test_affinity_snapshot import settings as settings, TOKEN
from tests.test_affinity import keep
from tests.fakes import FakeClock, FakeLLM


async def test_go_affinity_matches_python_and_hides_raw_state(make_client, settings, monkeypatch):
    binary = os.environ.get('EIDOLON_AFFINITY_BINARY')
    if not binary:
        pytest.skip('set EIDOLON_AFFINITY_BINARY for real Go affinity differential acceptance')
    clock = FakeClock(datetime(2026,10,27,12,0,0,123456,tzinfo=UTC))
    original = make_client(FakeLLM(), clock=clock)
    other = make_client(FakeLLM(), 'other', clock=clock)
    assert isinstance(original._transport, httpx.ASGITransport)
    app = cast(FastAPI, original._transport.app)
    server = uvicorn.Server(uvicorn.Config(app,lifespan='off',log_level='error'))
    listener = socket.socket()
    listener.bind(('127.0.0.1',0))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError('upstream stopped')
                await asyncio.sleep(.01)
        root = Path(__file__).resolve().parents[2]
        process = await asyncio.create_subprocess_exec(binary,'-listen','127.0.0.1:0',
            '-legacy',f'http://127.0.0.1:{listener.getsockname()[1]}',
            '-packs',f'{root}/core/packs,{root}/core/testdata/packs',
            '-affinity-token-file',str(settings.core_service_token_path),stderr=asyncio.subprocess.PIPE)
        assert process.stderr is not None
        ready = await asyncio.wait_for(process.stderr.readline(),10)
        assert b'catalog listening on ' in ready, ready
        origin = 'http://'+ready.decode().strip().split('catalog listening on ',1)[1]
        async with httpx.AsyncClient(base_url=origin,trust_env=False,
            cookies={c.name:c.value for c in original.cookies.jar if c.value is not None}) as gateway:
            path='/api/characters/mano/affinity'
            assert (await gateway.get(path)).json() == (await original.get(path)).json() == {'tier':'trust','cooldown_until':None}
            cases=[(v,elapsed,cooling) for v in (0,9.999999,10,19.999999,20,39.999999,40,49.999999,50,64.999999,65,84.999999,85,100)
                   for elapsed in (-86400,0,.000001,3600,43200,86400,8640000) for cooling in (False,True)]
            rng=random.Random(27)
            cases += [(rng.random()*10,rng.random()*86400,True) for _ in range(60)]
            for value,elapsed,cooling in cases:
                before=(clock.now-timedelta(seconds=elapsed)).astimezone(timezone(timedelta(hours=9)))
                keep(settings.database_path,'producer',value,before,cooling=cooling)
                direct=await original.get(path)
                computed=await gateway.get(path,headers={'Authorization':'Bearer client-value-must-be-ignored'})
                scenario=(value,elapsed,cooling)
                assert computed.status_code == direct.status_code == 200, (scenario,computed.text)
                assert computed.json() == direct.json(), (scenario,computed.json(),direct.json())
                assert set(computed.json()) == {'tier','cooldown_until'}, scenario
            for observed in (clock.now.replace(microsecond=0), clock.now.astimezone(timezone(timedelta(hours=9))).replace(microsecond=100000)):
                clock.now = observed
                keep(settings.database_path,'producer',0,clock.now-timedelta(hours=12),cooling=True)
                computed, direct = await gateway.get(path), await original.get(path)
                assert computed.json() == direct.json(), ('ISO precision/offset', observed, computed.text, direct.text)
            keep(settings.database_path,'producer',0,clock.now,cooling=True)
            keep(settings.database_path,'producer',85,clock.now,character='kogane')
            keep(settings.database_path,'other',85,clock.now)
            from tests.test_delete_account import user_id
            other_id = user_id(settings.database_path, 'other')
            assert (await gateway.get(path, params={'user_id':other_id})).json()['tier']=='cold'
            assert (await gateway.get('/api/characters/kogane/affinity')).json()['tier']=='intimate'
            for client in (other,original):
                gateway.cookies={c.name:c.value for c in client.cookies.jar if c.value is not None}
                assert (await gateway.get(path)).json() == (await client.get(path)).json()
            for route in ('/api/_core/affinity/mano','/api/%5fcore/affinity/mano'):
                response=await gateway.get(route,headers={'Authorization':'Bearer '+TOKEN})
                assert response.status_code==404 and 'value' not in response.text, route
            for params,headers in (({'work_id':'demo'},{}),({}, {'X-Work-ID':'demo'})):
                response=await gateway.get(path,params=params,headers=headers)
                assert response.status_code==409, response.text
            with monkeypatch.context() as patch:
                patch.setattr(type(app.state.backend.config),'affinity_on',property(lambda _:False))
                assert (await gateway.get(path)).json() is None
                assert (await original.get(path)).json() is None
            from eidolon.affinity import Affinities
            async def public_broken(*args):
                raise RuntimeError('public Python calculation deliberately unavailable')
            with monkeypatch.context() as patch:
                patch.setattr(Affinities,'status',public_broken)
                computed=await gateway.get(path)
                assert computed.status_code==200 and computed.json()['tier']=='cold', computed.text
            gateway.cookies.clear()
            unauth=await gateway.get(path)
            assert unauth.status_code==401 and unauth.json()=={'detail':'请先登录'}
            print(f'PASS Go affinity differential: {len(cases)+2} fixed-time states, user/character/SC boundaries, private route blocking, no Python public computation')
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(),5)
            except TimeoutError:
                process.kill()
                await process.wait()
        server.should_exit=True
        try:
            await asyncio.wait_for(task,5)
        finally:
            listener.close()
