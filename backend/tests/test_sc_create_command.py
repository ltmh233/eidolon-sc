"""Purpose-limited creation against real authentication and synthetic SQLite."""
from dataclasses import replace
import uuid
from typing import cast
from fastapi import FastAPI

import pytest

from tests.fakes import FakeLLM
from tests.test_character_snapshot import CONFIG, export
from tests.test_sc_play_scope import gameplay_snapshot

READ = '1' * 64
WRITE = '2' * 64
PATH = '/api/_core/sc-conversation-create'


@pytest.fixture
def settings(settings, tmp_path):
    snapshot = tmp_path / 'content.json'
    result = export(CONFIG, snapshot, with_gifts=True)
    assert result.returncode == 0, result.stderr.decode()
    read, write = tmp_path / 'read-token', tmp_path / 'create-token'
    for path, token in ((read, READ), (write, WRITE)):
        path.write_text(token)
        path.chmod(0o600)
    return replace(settings, characters_path=CONFIG, character_snapshot_path=snapshot,
                   core_service_token_path=read, core_sc_create_token_path=write)


async def prepare(client):
    response = await client.post(PATH + '/prepare', json={'character_id':'mano'}, headers={'Authorization':'Bearer '+WRITE})
    assert response.status_code == 200, response.text
    return response.json()


def command(context):
    return {**context, 'operation':'CreateSCConversation', 'command_id':uuid.uuid4().hex}


async def test_create_capability_commits_exactly_one_bound_row(make_client, settings):
    client = make_client(FakeLLM())
    before = gameplay_snapshot(settings.database_path)
    context = await prepare(client)
    assert gameplay_snapshot(settings.database_path) == before
    body = command(context)
    response = await client.post(PATH+'/execute', json=body, headers={'Authorization':'Bearer '+WRITE})
    assert response.status_code == 201, response.text
    row = response.json()
    assert row['id'] == body['command_id'] and row['user_id'] == context['user_id']
    assert row['character_id'] == 'mano' and row['title'] is None
    assert (await client.get('/api/conversations/'+row['id']+'/messages')).status_code == 200
    after = gameplay_snapshot(settings.database_path)
    assert len(after.pop('conversations')) == 1
    before.pop('conversations')
    assert after == before
    # Explicit ID collision rolls back; it is not an idempotent response cache.
    duplicate = await client.post(PATH+'/execute', json=body, headers={'Authorization':'Bearer '+WRITE})
    assert duplicate.status_code == 409
    assert len((await client.get('/api/conversations', params={'character_id':'mano'})).json()) == 1


async def test_read_capability_and_disabled_create_cannot_write(make_client, settings):
    client = make_client(FakeLLM())
    context = await prepare(client)
    before = gameplay_snapshot(settings.database_path)
    for step, body in (('prepare', {'character_id':'mano'}), ('execute', command(context))):
        for token in (READ, 'wrong'):
            response = await client.post(PATH+'/'+step, json=body, headers={'Authorization':'Bearer '+token})
            assert response.status_code == 404, (step, token == READ, response.text)
    disabled = make_client(FakeLLM(), settings=replace(settings, core_sc_create_token_path=None))
    assert (await disabled.post(PATH+'/prepare', json={'character_id':'mano'}, headers={'Authorization':'Bearer '+WRITE})).status_code == 404
    assert gameplay_snapshot(settings.database_path) == before


async def test_execute_revalidates_user_scope_content_and_shape(make_client, settings):
    client = make_client(FakeLLM())
    body = command(await prepare(client))
    before = gameplay_snapshot(settings.database_path)
    for key, value in (('user_id','other'), ('work_id','demo'), ('content_digest','0'*64),
                       ('character_id','missing'), ('operation','SQL'), ('version',2),
                       ('command_id','not-uuid'), ('extra','private')):
        response = await client.post(PATH+'/execute', json=body | {key:value}, headers={'Authorization':'Bearer '+WRITE})
        assert response.status_code in (409, 422), (key, response.text)
        assert gameplay_snapshot(settings.database_path) == before, key
    for origin in ('https://other.invalid', ''):
        response = await client.post(PATH+'/execute', json=body, headers={'Authorization':'Bearer '+WRITE, 'Origin':origin})
        assert response.status_code == 403, origin
        assert gameplay_snapshot(settings.database_path) == before, origin


def test_equal_read_write_credentials_refuse_startup(make_client, settings):
    with pytest.raises(RuntimeError, match='distinct'):
        make_client(FakeLLM(), settings=replace(settings, core_sc_create_token_path=settings.core_service_token_path))


async def test_execute_after_user_removed_has_no_write_authority(make_client, settings):
    client = make_client(FakeLLM())
    body = command(await prepare(client))
    import httpx
    assert isinstance(client._transport,httpx.ASGITransport)
    await cast(FastAPI,client._transport.app).state.backend.store.delete_user(body['user_id'])
    before = gameplay_snapshot(settings.database_path)
    response = await client.post(PATH+'/execute',json=body,headers={'Authorization':'Bearer '+WRITE})
    assert response.status_code == 401, response.text
    assert gameplay_snapshot(settings.database_path) == before


def test_create_rejects_unmatched_legacy_startup(make_client, settings):
    with pytest.raises(RuntimeError,match='loaded v3 content'):
        make_client(FakeLLM(),settings=replace(settings,character_snapshot_path=None))
