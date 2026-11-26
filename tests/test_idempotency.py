import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Base
from app.models import Campaign, MutationOperation, Stream, StreamOffer
from tests.test_editor_api import published_stream as published_stream

BASE = "https://keitaro.test/admin_api/v1"
CREATE_BODY = {"name": "Idempotent", "geo": "AU", "offer_id": 42}


@pytest.fixture
async def operation_key(app: FastAPI) -> AsyncIterator[str]:
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    key = uuid4()
    try:
        yield str(key)
    finally:
        async with app.state.sessionmaker() as session:
            operation = await session.get(MutationOperation, key)
            if operation and operation.kind == "create" and operation.result:
                campaign_id = operation.result["id"]
                stream_ids = select(Stream.id).where(Stream.campaign_id == campaign_id)
                await session.execute(
                    delete(StreamOffer).where(StreamOffer.stream_id.in_(stream_ids))
                )
                await session.execute(delete(Stream).where(Stream.campaign_id == campaign_id))
                await session.execute(delete(Campaign).where(Campaign.id == campaign_id))
            await session.execute(delete(MutationOperation).where(MutationOperation.key == key))
            await session.commit()


def mock_catalogs(router: respx.MockRouter) -> None:
    router.get(f"{BASE}/offers").respond(
        200, json=[{"id": 42, "name": "Offer Forty Two", "state": "active"}]
    )
    router.get(f"{BASE}/domains").respond(200, json=[{"id": 1, "name": "domain"}])
    router.get(f"{BASE}/groups", params={"type": "campaigns"}).respond(
        200, json=[{"id": 2, "name": "group"}]
    )
    router.get(f"{BASE}/traffic_sources").respond(200, json=[{"id": 3, "name": "source"}])


async def test_create_retry_checks_durable_alias_and_only_resumes_when_visible(
    client: httpx.AsyncClient,
    app: FastAPI,
    respx_mock: respx.MockRouter,
    operation_key: str,
) -> None:
    mock_catalogs(respx_mock)
    post = respx_mock.post(f"{BASE}/campaigns").mock(side_effect=httpx.ReadTimeout("timeout"))
    lookup = respx_mock.get(f"{BASE}/campaigns").respond(200, json=[])
    streams = respx_mock.post(f"{BASE}/streams").mock(
        side_effect=[httpx.Response(201, json={"id": 201}), httpx.Response(201, json={"id": 202})]
    )
    headers = {"Idempotency-Key": operation_key}
    first = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert first.status_code == 502
    assert first.json()["code"] == "campaign_create_uncertain"
    sent_body = json.loads(post.calls.last.request.content)
    async with app.state.sessionmaker() as session:
        operation = await session.get(MutationOperation, UUID(operation_key))
        assert operation is not None
        assert operation.alias == sent_body["alias"]
        assert operation.request == CREATE_BODY
    missing = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert missing.json()["code"] == "campaign_create_uncertain"
    lookup.respond(200, json=[{"id": 100, "name": sent_body["name"], "alias": sent_body["alias"]}])
    created = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert created.status_code == 201
    assert created.json()["alias"] == sent_body["alias"]
    upstream_calls = len(respx_mock.calls)
    replay = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert replay.status_code == 201
    assert replay.json() == created.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = await client.post(
        "/api/campaigns", json={**CREATE_BODY, "geo": "DE"}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert len(respx_mock.calls) == upstream_calls
    assert post.call_count == 1
    assert streams.call_count == 2


async def test_create_concurrent_retries_share_result_after_confirmed_collision(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, operation_key: str
) -> None:
    mock_catalogs(respx_mock)
    post = respx_mock.post(f"{BASE}/campaigns").mock(
        side_effect=[httpx.Response(409), httpx.Response(201, json={"id": 100})]
    )
    streams = respx_mock.post(f"{BASE}/streams").mock(
        side_effect=[httpx.Response(201, json={"id": 201}), httpx.Response(201, json={"id": 202})]
    )
    headers = {"Idempotency-Key": operation_key}
    responses = await asyncio.gather(
        client.post("/api/campaigns", json=CREATE_BODY, headers=headers),
        client.post("/api/campaigns", json=CREATE_BODY, headers=headers),
    )
    assert [response.status_code for response in responses] == [201, 201]
    assert sum("Idempotency-Replayed" in response.headers for response in responses) == 1
    assert responses[0].json() == responses[1].json()
    assert post.call_count == 2
    aliases = [json.loads(call.request.content)["alias"] for call in post.calls]
    assert aliases[0] != aliases[1]
    assert aliases[1] == responses[0].json()["alias"]
    assert streams.call_count == 2


async def test_push_replays_old_success_after_another_publication(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
    operation_key: str,
) -> None:
    _, stream_id, keitaro_id = published_stream
    mock_catalogs(respx_mock)
    updated = respx_mock.put(f"{BASE}/streams/{keitaro_id}").respond(204)
    path = f"/api/streams/{stream_id}/push"
    headers = {"Idempotency-Key": operation_key}
    original = [
        {"offer_id": 9_800_010, "status": "active"},
        {"offer_id": 9_800_011, "status": "active"},
        {"offer_id": 9_800_012, "status": "active"},
    ]
    draft = {"offers": [*original, {"offer_id": 42, "status": "added"}]}
    not_applied = await client.post(
        path, json=draft, headers={**headers, "Idempotency-Replay-Only": "true"}
    )
    assert not_applied.status_code == 409
    assert not_applied.json()["code"] == "idempotency_result_unavailable"
    assert not updated.called
    first, second = await asyncio.gather(
        client.post(path, json=draft, headers=headers),
        client.post(path, json=draft, headers=headers),
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert sum("Idempotency-Replayed" in item.headers for item in (first, second)) == 1
    assert updated.call_count == 1
    next_body = {"offers": [*original, {"offer_id": 42, "status": "removed"}]}
    publication = await client.post(path, json=next_body)
    assert publication.status_code == 200
    replay = await client.post(
        path, json=draft, headers={**headers, "Idempotency-Replay-Only": "true"}
    )
    assert replay.json() == first.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    conflict = await client.post(path, json=next_body, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert updated.call_count == 2


@pytest.mark.parametrize(
    "share_token",
    ["-1", "101", "-0.5", "100.5", '"101"', '"-0.5"', '"100.5"', "-1e-400", "100.000000000000001"],
)
async def test_fetch_rejects_out_of_range_offer_weights(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
    share_token: str,
) -> None:
    campaign_id, _, keitaro_stream_id = published_stream
    upstream = json.dumps(
        [
            {
                "campaign_id": 9_800_000 + campaign_id,
                "id": keitaro_stream_id,
                "name": "Invalid weights",
                "position": 0,
                "schema": "landings",
                "offers": [{"offer_id": 9_800_010, "share": None, "state": "active"}],
            }
        ]
    ).replace('"share": null', f'"share": {share_token}')
    respx_mock.get(f"{BASE}/campaigns/{9_800_000 + campaign_id}/streams").respond(
        200, content=upstream, headers={"Content-Type": "application/json"}
    )
    result = await client.post(f"/api/campaigns/{campaign_id}/fetch")
    assert result.status_code == 502
    assert result.json() == {
        "code": "keitaro_invalid_offer_weights",
        "detail": "Offer weights in Keitaro are invalid. Check them in the tracker "
        "and fetch the streams again.",
    }


def geo_stream_details(filter_payload: str | list[str]) -> dict[str, object]:
    return {
        "id": 201,
        "campaign_id": 100,
        "name": "Geo redirect",
        "position": 0,
        "schema": "redirect",
        "offers": [],
        "filters": [{"name": "country", "mode": "accept", "payload": filter_payload}],
        "landings": [],
        "type": "regular",
        "state": "active",
        "action_type": "http",
        "action_payload": "https://google.com",
    }


@pytest.mark.parametrize("filter_payload", ["AU", '["AU"]', ["AU"]])
async def test_create_after_interruption_reconciles_partially_created_streams(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    operation_key: str,
    filter_payload: str | list[str],
) -> None:
    mock_catalogs(respx_mock)
    headers = {"Idempotency-Key": operation_key}
    create = respx_mock.post(f"{BASE}/campaigns").respond(201, json={"id": 100})

    sent_positions: list[int] = []

    def create_stream(request: httpx.Request) -> httpx.Response:
        position = json.loads(request.content)["position"]
        sent_positions.append(position)
        if position == 0:
            raise asyncio.CancelledError()
        return httpx.Response(201, json={"id": 202})

    respx_mock.post(f"{BASE}/streams").mock(side_effect=create_stream)
    with pytest.raises(asyncio.CancelledError):
        await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    original = json.loads(create.calls.last.request.content)
    respx_mock.get(f"{BASE}/campaigns").respond(
        200, json=[{"id": 100, "alias": original["alias"], "name": original["name"]}]
    )
    stream_lookup = respx_mock.get(f"{BASE}/campaigns/100/streams").respond(200, json=[])
    incomplete = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert incomplete.status_code == 502
    assert incomplete.json()["code"] == "campaign_create_uncertain"
    assert original["alias"] in incomplete.json()["detail"]
    assert sent_positions == [0]
    details = geo_stream_details(filter_payload)
    stream_lookup.respond(200, json=[details])
    detail_lookup = respx_mock.get(f"{BASE}/streams/201")
    for mismatch in (
        {"action_payload": "https://example.test"},
        {"action_type": "meta"},
        {"type": "forced"},
        {"state": "disabled"},
        {"filters": [{"name": "country", "mode": "accept", "payload": ["DE"]}]},
        {"filters": [{"name": "language", "mode": "accept", "payload": ["AU"]}]},
        {"filters": [{"name": "country", "mode": "reject", "payload": ["AU"]}]},
    ):
        detail_lookup.respond(200, json={**details, **mismatch})
        rejected = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
        assert rejected.json()["code"] == "campaign_create_uncertain"
        assert sent_positions == [0]

    detail_lookup.respond(200, json=details)
    complete = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert complete.status_code == 201
    assert create.call_count == 1
    assert sent_positions == [0, 1]


async def test_create_retries_unconfirmed_compensation_before_starting_again(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, operation_key: str
) -> None:
    mock_catalogs(respx_mock)
    headers = {"Idempotency-Key": operation_key}
    start = respx_mock.post(f"{BASE}/campaigns").mock(
        side_effect=[httpx.Response(201, json={"id": 100}), httpx.Response(201, json={"id": 101})]
    )
    respx_mock.post(f"{BASE}/streams").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(201, json={"id": 211}),
            httpx.Response(201, json={"id": 212}),
        ]
    )
    delete_call = respx_mock.delete(f"{BASE}/campaigns/100").mock(
        side_effect=[httpx.ReadTimeout("delete response lost"), httpx.Response(404)]
    )
    failed = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert failed.json()["code"] == "campaign_create_uncertain"
    assert start.call_count == 1
    retried = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert retried.status_code == 201
    assert retried.json()["keitaro_id"] == 101
    assert delete_call.call_count == start.call_count == 2


async def test_push_local_failure_after_keitaro_update_keeps_key_retryable(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
    operation_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id, stream_id, keitaro_id = published_stream
    mock_catalogs(respx_mock)
    updated = respx_mock.put(f"{BASE}/streams/{keitaro_id}").respond(204)
    path = f"/api/streams/{stream_id}/push"
    headers = {"Idempotency-Key": operation_key}
    draft = {
        "offers": [
            {"offer_id": 9_800_010, "status": "active"},
            {"offer_id": 9_800_011, "status": "active"},
            {"offer_id": 9_800_012, "status": "active"},
            {"offer_id": 42, "status": "added"},
        ]
    }

    commit = AsyncSession.commit
    fail_next_push_commit = True

    async def commit_with_failure(session: AsyncSession) -> None:
        nonlocal fail_next_push_commit
        has_result = any(
            isinstance(row, MutationOperation) and row.result is not None for row in session.dirty
        )
        if has_result and fail_next_push_commit:
            fail_next_push_commit = False
            raise SQLAlchemyError("synthetic commit failure")
        await commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_with_failure)
    failed = await client.post(path, json=draft, headers=headers)
    assert failed.status_code == 500
    assert failed.json()["code"] == "editor_persistence_error"
    assert "Fetch" in failed.json()["detail"]
    before_retry = await client.get(f"/api/campaigns/{campaign_id}/streams")
    assert [offer["offer_id"] for offer in before_retry.json()[0]["offers"]] == [
        9_800_010,
        9_800_011,
        9_800_012,
    ]
    replay = await client.post(
        path, json=draft, headers={**headers, "Idempotency-Replay-Only": "true"}
    )
    assert replay.json()["code"] == "idempotency_result_unavailable"
    assert updated.call_count == 1
    succeeded = await client.post(path, json=draft, headers=headers)
    assert succeeded.status_code == 200
    assert succeeded.json()["offers"][-1]["offer_id"] == 42
    assert updated.call_count == 2


async def test_create_after_database_disconnect_does_not_compensate(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    operation_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_catalogs(respx_mock)
    headers = {"Idempotency-Key": operation_key}
    create = respx_mock.post(f"{BASE}/campaigns").respond(201, json={"id": 100})
    streams = respx_mock.post(f"{BASE}/streams").mock(
        side_effect=[httpx.Response(201, json={"id": 201}), httpx.Response(201, json={"id": 202})]
    )
    delete_call = respx_mock.delete(f"{BASE}/campaigns/100").respond(204)
    commit = AsyncSession.commit
    fail_once = True

    async def commit_with_disconnect(session: AsyncSession) -> None:
        nonlocal fail_once
        if fail_once and any(
            isinstance(row, MutationOperation) and row.geo_stream_id == 201 for row in session.dirty
        ):
            fail_once = False
            connection = await session.connection()
            await connection.invalidate()
            raise DBAPIError(None, None, RuntimeError("disconnect"), connection_invalidated=True)
        await commit(session)

    monkeypatch.setattr(AsyncSession, "commit", commit_with_disconnect)
    failed = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert failed.status_code == 502
    assert failed.json()["code"] == "campaign_create_uncertain"
    assert not delete_call.called
    assert streams.call_count == 1

    original = json.loads(create.calls.last.request.content)
    respx_mock.get(f"{BASE}/campaigns").respond(
        200, json=[{"id": 100, "alias": original["alias"], "name": original["name"]}]
    )
    details = geo_stream_details("AU")
    respx_mock.get(f"{BASE}/campaigns/100/streams").respond(200, json=[details])
    respx_mock.get(f"{BASE}/streams/201").respond(200, json=details)
    recovered = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert recovered.status_code == 201
    assert create.call_count == 1
    assert streams.call_count == 2
    assert not delete_call.called


async def test_create_rejects_incompatible_recovered_offers_stream(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    operation_key: str,
) -> None:
    mock_catalogs(respx_mock)
    headers = {"Idempotency-Key": operation_key}
    create = respx_mock.post(f"{BASE}/campaigns").respond(201, json={"id": 100})
    sent_positions: list[int] = []

    def create_stream(request: httpx.Request) -> httpx.Response:
        position = json.loads(request.content)["position"]
        sent_positions.append(position)
        if position == 1:
            raise asyncio.CancelledError()
        return httpx.Response(201, json={"id": 201})

    respx_mock.post(f"{BASE}/streams").mock(side_effect=create_stream)
    with pytest.raises(asyncio.CancelledError):
        await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)

    original = json.loads(create.calls.last.request.content)
    respx_mock.get(f"{BASE}/campaigns").respond(
        200, json=[{"id": 100, "alias": original["alias"], "name": original["name"]}]
    )
    geo = geo_stream_details(["AU"])
    offers = {
        **geo,
        "id": 202,
        "position": 1,
        "name": "Offers",
        "schema": "landings",
        "filters": [],
        "offers": [{"offer_id": 42, "share": 100, "state": "active"}],
    }
    respx_mock.get(f"{BASE}/campaigns/100/streams").respond(200, json=[geo, offers])
    respx_mock.get(f"{BASE}/streams/201").respond(200, json=geo)
    details = respx_mock.get(f"{BASE}/streams/202")
    for mismatch in (
        {"landings": [{"landing_id": 99}]},
        {"filters": geo["filters"]},
        {"offers": [{"offer_id": 99, "share": 100, "state": "active"}]},
        {"state": "disabled"},
        {"type": "forced"},
    ):
        details.respond(200, json={**offers, **mismatch})
        rejected = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
        assert rejected.json()["code"] == "campaign_create_uncertain"
        assert sent_positions == [0, 1]

    details.respond(200, json=offers)
    recovered = await client.post("/api/campaigns", json=CREATE_BODY, headers=headers)
    assert recovered.status_code == 201
    assert sent_positions == [0, 1]
