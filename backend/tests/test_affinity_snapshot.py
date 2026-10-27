"""Raw affinity is available only to the trusted core and authenticated SC user."""
from dataclasses import replace
from datetime import timedelta
import sqlite3

import pytest

from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM
from tests.test_affinity import keep

TOKEN = '1' * 64


@pytest.fixture
def settings(settings, tmp_path):
    token = tmp_path / 'core-token'
    token.write_text(TOKEN)
    token.chmod(0o600)
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True, core_service_token_path=token)


async def test_snapshot_requires_service_and_user_and_returns_raw_not_recovered(make_client, settings):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    keep(settings.database_path, 'producer', 0, clock.now-timedelta(hours=12), cooling=True)
    path = '/api/_core/affinity/mano'
    for headers in ({}, {'Authorization':'Bearer wrong'}, [('Authorization','Bearer '+TOKEN),('Authorization','Bearer wrong')]):
        response = await client.get(path, headers=headers)
        assert response.status_code == 404, headers
        assert 'value' not in response.text, headers
    response = await client.get(path+'?user_id=other', headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['work_id'] == 'sc' and data['character_id'] == 'mano' and data['version'] == 1
    assert data['state']['value'] == 0 and data['state']['cooling'] is True
    assert data['observed_at'] == clock.now.isoformat()
    assert data['state']['updated_at_us'] == int((clock.now-timedelta(hours=12)).timestamp()*1e6)
    assert response.headers['cache-control'] == 'no-store'
    for route, expected in ((path+'?work_id=demo',409),('/api/_core/affinity/missing',404)):
        assert (await client.get(route,headers={'Authorization':'Bearer '+TOKEN})).status_code == expected, route
    public = await client.get('/api/characters/mano/affinity')
    assert set(public.json()) == {'tier','cooldown_until'}
    assert 'value' not in public.text
    assert '/api/_core/' not in (await client.get('/openapi.json')).text
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT value,cooling FROM affinities').fetchone() == (0,1)


async def test_private_snapshot_requires_user_and_can_be_disabled(make_client, settings):
    client = make_client(FakeLLM())
    unauth = make_client(FakeLLM(), user=None)
    path = '/api/_core/affinity/mano'
    response = await unauth.get(path, headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 401 and response.json() == {'detail':'请先登录'}
    disabled = make_client(FakeLLM(), settings=replace(settings, core_service_token_path=None))
    response = await disabled.get(path, headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 404 and 'value' not in response.text
    assert (await client.get('/api/characters/mano/affinity')).status_code == 200


def test_service_credentials_are_owner_only_and_must_be_real_random_key_shape(tmp_path):
    from eidolon.affinity_auth import affinity_service_auth
    path = tmp_path/'token'
    for text,mode in ((TOKEN,0o644),('short',0o600),('A'*64,0o600)):
        path.write_text(text)
        path.chmod(mode)
        with pytest.raises(RuntimeError,match='invalid affinity service credential'):
            affinity_service_auth(path)
    path.write_text(TOKEN+'\n')
    path.chmod(0o600)
    assert callable(affinity_service_auth(path))
