"""Campaign creation and editor flow against PostgreSQL and a mocked tracker."""

import json
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from tests.test_campaign_create import db_session as db_session


@pytest.fixture
async def journey_client(
    client: httpx.AsyncClient, app: FastAPI, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        yield client
    finally:
        del app.dependency_overrides[get_session]


async def test_create_fetch_edit_publish_and_reconcile(
    journey_client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    client = journey_client
    base = "https://keitaro.test/admin_api/v1"
    catalog = [{"id": i, "name": f"Offer {i}", "state": "active"} for i in range(101, 105)]
    respx_mock.get(f"{base}/offers").respond(json=catalog)
    for path, item_id in (("domains", 1), ("groups", 2), ("traffic_sources", 3)):
        respx_mock.get(f"{base}/{path}").respond(json=[{"id": item_id, "name": path}])
    create = respx_mock.post(f"{base}/campaigns").respond(201, json={"id": 201})
    remote_streams: list[dict[str, object]] = []

    def create_stream(request: httpx.Request) -> httpx.Response:
        stream: dict[str, object] = json.loads(request.content)
        stream_id = 301 + len(remote_streams)
        stream["id"] = stream_id
        remote_streams.append(stream)
        return httpx.Response(201, json={"id": stream_id})

    def fetch_streams(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=remote_streams)

    def update_stream(request: httpx.Request) -> httpx.Response:
        remote_streams[1]["offers"] = json.loads(request.content)["offers"]
        return httpx.Response(200, json=remote_streams[1])

    respx_mock.post(f"{base}/streams").mock(side_effect=create_stream)
    respx_mock.get(f"{base}/campaigns/201/streams").mock(side_effect=fetch_streams)
    update = respx_mock.put(f"{base}/streams/302").mock(side_effect=update_stream)

    found = await client.get("/api/offers/search", params={"q": "101"})
    assert [offer["id"] for offer in found.json()] == [101]
    found = await client.get("/api/offers/search", params={"q": "Offer 10"})
    assert [offer["id"] for offer in found.json()] == [101, 102, 103, 104]

    response = await client.post(
        "/api/campaigns",
        headers={"Idempotency-Key": str(uuid4())},
        json={"name": "Example", "geo": "AU", "offer_id": 101},
    )
    assert response.status_code == 201, response.text
    campaign_id = response.json()["id"]
    payload = json.loads(create.calls.last.request.content)
    assert (payload["domain_id"], payload["group_id"], payload["traffic_source_id"]) == (1, 2, 3)
    assert payload["type"] == "position"
    geo_flow, offers_flow = remote_streams
    assert (geo_flow["position"], offers_flow["position"]) == (0, 1)
    assert geo_flow["action_payload"] == "https://google.com"
    assert geo_flow["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert offers_flow["filters"] == []
    assert offers_flow["offers"] == [{"offer_id": 101, "share": 100, "state": "active"}]

    campaign_url = f"/api/campaigns/{campaign_id}"
    response = await client.post(f"{campaign_url}/fetch")
    assert response.status_code == 200, response.text
    redirect, stream = response.json()
    assert redirect["offers"] == []
    stream_url = f"/api/streams/{stream['id']}"

    async def preview(offers: list[dict[str, int | str]]) -> list[dict[str, object]]:
        response = await client.post(f"{stream_url}/preview", json={"offers": offers})
        assert response.status_code == 200, response.text
        result: list[dict[str, object]] = response.json()["offers"]
        return result

    async def push(offers: list[dict[str, int | str]]) -> list[dict[str, object]]:
        response = await client.post(
            f"{stream_url}/push", headers={"Idempotency-Key": str(uuid4())}, json={"offers": offers}
        )
        assert response.status_code == 200, response.text
        result: list[dict[str, object]] = response.json()["offers"]
        return result

    draft: list[dict[str, int | str]] = [{"offer_id": 101, "status": "active"}]
    expected_weights = ([50, 50], [33, 33, 34], [25, 25, 25, 25])
    for offer_id, weights in zip(range(102, 105), expected_weights, strict=True):
        draft.append({"offer_id": offer_id, "status": "added"})
        assert [offer["weight"] for offer in await preview(draft)] == weights
    assert update.call_count == 0
    assert [offer["weight"] for offer in await push(draft)] == [25, 25, 25, 25]
    assert offers_flow["offers"] == [
        {"offer_id": i, "share": 25, "state": "active"} for i in range(101, 105)
    ]

    response = await client.put(
        f"{stream_url}/offers/101/pin",
        headers={"X-Stream-Dirty": "false"},
        json={"pinned": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["offers"][0]["pinned"] is True
    removed: list[dict[str, int | str]] = [
        {"offer_id": i, "status": "removed" if i == 102 else "active"} for i in range(101, 105)
    ]
    removed_preview = {offer["offer_id"]: offer for offer in await preview(removed)}
    assert removed_preview[102]["weight"] == 0
    assert removed_preview[101]["weight"] == 25
    assert {removed_preview[103]["weight"], removed_preview[104]["weight"]} == {37, 38}
    assert update.call_count == 1
    response = await client.get(f"{campaign_url}/streams")
    assert [offer["weight"] for offer in response.json()[1]["offers"]] == [25, 25, 25, 25]

    published = {offer["offer_id"]: offer for offer in await push(removed)}
    assert published[102]["status"] == "removed"
    assert published[102]["weight"] == 0
    active_upstream = json.loads(update.calls.last.request.content)["offers"]
    assert {offer["offer_id"] for offer in active_upstream} == {101, 103, 104}

    restored: list[dict[str, int | str]] = [
        {"offer_id": i, "status": "active"} for i in range(101, 105)
    ]
    assert [offer["weight"] for offer in await preview(restored)] == [25, 25, 25, 25]
    assert [offer["status"] for offer in await push(restored)] == ["active"] * 4
    active_upstream = json.loads(update.calls.last.request.content)["offers"]
    assert {offer["offer_id"] for offer in active_upstream} == {101, 102, 103, 104}

    offers_flow["offers"] = [offer for offer in active_upstream if offer["offer_id"] != 103]
    response = await client.post(f"{campaign_url}/fetch")
    assert response.status_code == 200, response.text
    synced = {offer["offer_id"]: offer for offer in response.json()[1]["offers"]}
    assert synced[103]["status"] == "removed"
    assert synced[103]["weight"] == 0
    assert [offer["weight"] for offer in await preview(restored)] == [25, 25, 25, 25]
