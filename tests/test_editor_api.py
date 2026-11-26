"""HTTP contract tests for the published editor API."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.db import Base
from app.models import Campaign, Stream, StreamOffer


@pytest.fixture
async def published_stream(app: FastAPI) -> AsyncIterator[tuple[int, int, int]]:
    """Seed and remove a uniquely identified campaign and its stream."""
    async with app.state.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with app.state.sessionmaker() as session:
        campaign = Campaign(
            keitaro_id=0,
            name="Editor API test",
            alias="editorapi",
            geo="AU",
        )
        session.add(campaign)
        await session.flush()
        campaign.keitaro_id = 9_800_000 + campaign.id
        stream = Stream(
            campaign_id=campaign.id,
            keitaro_id=9_900_000 + campaign.id,
            name="Offer stream",
            position=0,
            has_offers=True,
        )
        session.add(stream)
        await session.flush()
        session.add_all(
            [
                StreamOffer(
                    stream_id=stream.id,
                    offer_id=9_800_010,
                    offer_name="Pinned",
                    status="active",
                    weight=25,
                    pinned=True,
                    position=0,
                ),
                StreamOffer(
                    stream_id=stream.id,
                    offer_id=9_800_011,
                    offer_name="Offer two",
                    status="active",
                    weight=37,
                    pinned=False,
                    position=1,
                ),
                StreamOffer(
                    stream_id=stream.id,
                    offer_id=9_800_012,
                    offer_name="Offer three",
                    status="active",
                    weight=38,
                    pinned=False,
                    position=2,
                ),
            ]
        )
        await session.commit()
        campaign_id, stream_id = campaign.id, stream.id
    try:
        yield campaign_id, stream_id, 9_900_000 + campaign_id
    finally:
        async with app.state.sessionmaker() as session:
            stream_ids = select(Stream.id).where(Stream.campaign_id == campaign_id)
            await session.execute(delete(StreamOffer).where(StreamOffer.stream_id.in_(stream_ids)))
            await session.execute(delete(Stream).where(Stream.campaign_id == campaign_id))
            await session.execute(delete(Campaign).where(Campaign.id == campaign_id))
            await session.commit()


async def test_preview_recalculates_without_changing_database(
    client: httpx.AsyncClient,
    app: FastAPI,
    published_stream: tuple[int, int, int],
) -> None:
    _, stream_id, _ = published_stream
    response = await client.post(
        f"/api/streams/{stream_id}/preview",
        json={
            "offers": [
                {"offer_id": 9_800_010, "status": "active"},
                {"offer_id": 9_800_011, "status": "active"},
                {"offer_id": 9_800_012, "status": "removed"},
            ]
        },
    )
    assert response.status_code == 200
    assert [offer["weight"] for offer in response.json()["offers"]] == [25, 75, 0]
    assert response.json()["offers"][0]["pinned"] is True
    async with app.state.sessionmaker() as session:
        rows = list(
            await session.scalars(
                select(StreamOffer)
                .where(StreamOffer.stream_id == stream_id)
                .order_by(StreamOffer.position)
            )
        )
    assert [(row.status, row.weight, row.pinned) for row in rows] == [
        ("active", 25, True),
        ("active", 37, False),
        ("active", 38, False),
    ]


async def test_push_sends_only_active_offers_and_archives_removed(
    client: httpx.AsyncClient,
    app: FastAPI,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    _, stream_id, keitaro_stream_id = published_stream
    route = respx_mock.put(f"https://keitaro.test/admin_api/v1/streams/{keitaro_stream_id}").mock(
        return_value=httpx.Response(204)
    )
    response = await client.post(
        f"/api/streams/{stream_id}/push",
        json={
            "offers": [
                {"offer_id": 9_800_010, "status": "active"},
                {"offer_id": 9_800_011, "status": "removed"},
                {"offer_id": 9_800_012, "status": "active"},
            ]
        },
    )
    assert response.status_code == 200
    assert route.call_count == 1
    request = route.calls.last.request
    body = json.loads(request.content)
    assert [offer["offer_id"] for offer in body["offers"]] == [9_800_010, 9_800_012]
    assert len(response.json()["offers"]) == 3
    async with app.state.sessionmaker() as session:
        rows = list(
            await session.scalars(
                select(StreamOffer)
                .where(StreamOffer.stream_id == stream_id)
                .order_by(StreamOffer.position)
            )
        )
    assert [(row.status, row.weight, row.pinned) for row in rows] == [
        ("active", 25, True),
        ("removed", 0, False),
        ("active", 75, False),
    ]


async def test_preview_rejects_duplicate_ids_and_push_makes_no_upstream_call(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    _, stream_id, keitaro_stream_id = published_stream
    route = respx_mock.put(f"https://keitaro.test/admin_api/v1/streams/{keitaro_stream_id}").mock(
        return_value=httpx.Response(204)
    )
    response = await client.post(
        f"/api/streams/{stream_id}/preview",
        json={
            "offers": [
                {"offer_id": 9_800_010, "status": "active"},
                {"offer_id": 9_800_010, "status": "active"},
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "editor_validation_error"
    assert route.call_count == 0


async def test_fetch_preserves_disabled_offers_and_redirect_stream(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    campaign_id, _, remote_stream_id = published_stream
    remote_campaign_id = 9_800_000 + campaign_id
    route = respx_mock.get(
        f"https://keitaro.test/admin_api/v1/campaigns/{remote_campaign_id}/streams"
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": remote_stream_id,
                    "name": "Offer stream",
                    "campaign_id": remote_campaign_id,
                    "position": 0,
                    "schema": "landings",
                    "offers": [
                        {
                            "offer_id": 9_800_010,
                            "share": 25,
                            "state": "active",
                            "name": "Pinned",
                        },
                        {
                            "offer_id": 9_800_013,
                            "share": 50,
                            "state": "disabled",
                            "name": "Disabled",
                        },
                    ],
                },
                {
                    "id": 9_800_003,
                    "name": "Redirect",
                    "campaign_id": remote_campaign_id,
                    "position": 1,
                    "schema": "redirect",
                    "offers": [],
                },
            ],
        )
    )
    response = await client.post(f"/api/campaigns/{campaign_id}/fetch")
    assert response.status_code == 200
    assert route.call_count == 1
    assert response.json()[1]["has_offers"] is False
    assert response.json()[1]["offers"] == []
    actual = [
        (offer["offer_id"], offer["status"], offer["weight"])
        for offer in response.json()[0]["offers"]
    ]
    assert sorted(actual) == [
        (9_800_010, "active", 25),
        (9_800_011, "removed", 0),
        (9_800_012, "removed", 0),
        (9_800_013, "removed", 0),
    ]


async def test_editing_after_fetch_with_incomplete_weights(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    campaign_id, stream_id, remote_stream_id = published_stream
    remote_campaign_id = 9_800_000 + campaign_id
    fetch = respx_mock.get(
        f"https://keitaro.test/admin_api/v1/campaigns/{remote_campaign_id}/streams"
    ).respond(
        json=[
            {
                "id": remote_stream_id,
                "name": "Offer stream",
                "campaign_id": remote_campaign_id,
                "position": 0,
                "schema": "landings",
                "offers": [
                    {"offer_id": 9_800_010, "share": 25, "state": "active"},
                    {"offer_id": 9_800_011, "share": 37, "state": "active"},
                ],
            }
        ]
    )
    response = await client.post(f"/api/campaigns/{campaign_id}/fetch")
    assert response.status_code == 200
    first, second, _ = response.json()[0]["offers"]
    assert first["pinned"]
    assert first["can_remove"]
    assert not second["can_remove"]
    assert not second["can_pin"]

    path = f"/api/streams/{stream_id}"
    headers = {"X-Stream-Dirty": "false"}
    response = await client.put(
        f"{path}/offers/9800010/pin", headers=headers, json={"pinned": False}
    )
    assert response.status_code == 200
    first, second, _ = response.json()["offers"]
    assert first["weight"] == 25
    assert second["weight"] == 37
    assert not first["pinned"]
    assert second["can_pin"]
    assert second["can_remove"]

    response = await client.put(
        f"{path}/offers/9800011/pin", headers=headers, json={"pinned": True}
    )
    assert response.status_code == 200
    first, second, _ = response.json()["offers"]
    assert second["pinned"]
    assert second["weight"] == 37
    assert not first["can_remove"]
    assert not first["can_pin"]

    response = await client.post(
        f"{path}/preview",
        json={
            "offers": [
                {"offer_id": 9_800_010, "status": "active"},
                {"offer_id": 9_800_011, "status": "removed"},
                {"offer_id": 9_800_012, "status": "removed"},
            ]
        },
    )
    assert response.status_code == 200
    assert [offer["weight"] for offer in response.json()["offers"]] == [100, 0, 0]
    assert respx_mock.calls.call_count == fetch.call_count == 1


async def test_pin_observes_dirty_header_and_updates_only_pin(
    client: httpx.AsyncClient,
    app: FastAPI,
    published_stream: tuple[int, int, int],
) -> None:
    _, stream_id, _ = published_stream
    path = f"/api/streams/{stream_id}/offers/9800010/pin"
    blocked = await client.put(path, headers={"X-Stream-Dirty": "true"}, json={"pinned": False})
    assert blocked.status_code == 409
    response = await client.put(path, headers={"X-Stream-Dirty": "false"}, json={"pinned": False})
    assert response.status_code == 200
    assert response.json()["id"] == stream_id
    async with app.state.sessionmaker() as session:
        rows = list(
            await session.scalars(
                select(StreamOffer)
                .where(StreamOffer.stream_id == stream_id)
                .order_by(StreamOffer.position)
            )
        )
    assert [(row.weight, row.pinned) for row in rows] == [
        (25, False),
        (37, False),
        (38, False),
    ]


async def test_delete_campaign_soft_deletes_after_upstream_success(
    client: httpx.AsyncClient,
    app: FastAPI,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    campaign_id, _, _ = published_stream
    remote_campaign_id = 9_800_000 + campaign_id
    route = respx_mock.delete(
        f"https://keitaro.test/admin_api/v1/campaigns/{remote_campaign_id}"
    ).mock(return_value=httpx.Response(204))
    response = await client.delete(f"/api/campaigns/{campaign_id}")
    assert response.status_code == 204
    assert route.call_count == 1
    async with app.state.sessionmaker() as session:
        campaign = await session.get(Campaign, campaign_id)
    assert campaign is not None
    assert campaign.deleted_at is not None


async def test_pin_waits_for_inflight_push(
    client: httpx.AsyncClient,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    """Pin must validate the offer state committed by the preceding Push."""
    import asyncio

    _, stream_id, keitaro_id = published_stream
    upstream_entered = asyncio.Event()
    release_upstream = asyncio.Event()

    async def hold_put(request: httpx.Request) -> httpx.Response:
        upstream_entered.set()
        await asyncio.wait_for(release_upstream.wait(), timeout=5)
        return httpx.Response(204)

    respx_mock.put(f"https://keitaro.test/admin_api/v1/streams/{keitaro_id}").mock(
        side_effect=hold_put
    )
    push_task = asyncio.create_task(
        client.post(
            f"/api/streams/{stream_id}/push",
            json={
                "offers": [
                    {"offer_id": 9_800_010, "status": "active"},
                    {"offer_id": 9_800_011, "status": "removed"},
                    {"offer_id": 9_800_012, "status": "active"},
                ]
            },
        )
    )
    await asyncio.wait_for(upstream_entered.wait(), timeout=5)
    pin_task = asyncio.create_task(
        client.put(
            f"/api/streams/{stream_id}/offers/9800011/pin",
            headers={"X-Stream-Dirty": "false"},
            json={"pinned": True},
        )
    )
    try:
        done, _ = await asyncio.wait({pin_task}, timeout=0.1)
        assert not done
    finally:
        release_upstream.set()
        pushed, pinned = await asyncio.gather(push_task, pin_task)
    assert pushed.status_code == 200
    assert pinned.status_code == 422


async def test_campaign_list_marks_missing_removed_and_delete_is_local(
    client: httpx.AsyncClient,
    app: FastAPI,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    campaign_id, _, _ = published_stream
    listed = respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").mock(
        return_value=httpx.Response(200, json=[])
    )
    deleted = respx_mock.delete(
        f"https://keitaro.test/admin_api/v1/campaigns/{9_800_000 + campaign_id}"
    ).mock(return_value=httpx.Response(204))
    response = await client.get("/api/campaigns")
    assert response.status_code == 200
    assert listed.call_count == 1
    listed_campaign = next(item for item in response.json() if item["id"] == campaign_id)
    assert listed_campaign["status"] == "removed"
    async with app.state.sessionmaker() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.status == "removed"
    response = await client.delete(f"/api/campaigns/{campaign_id}")
    assert response.status_code == 204
    assert deleted.call_count == 0
    async with app.state.sessionmaker() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.deleted_at is not None


async def test_campaign_list_restores_present_campaign(
    client: httpx.AsyncClient,
    app: FastAPI,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    campaign_id, _, _ = published_stream
    async with app.state.sessionmaker() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        campaign.status = "removed"
        await session.commit()
    respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").respond(
        200, json=[{"id": 9_800_000 + campaign_id, "name": "Editor API test"}]
    )
    response = await client.get("/api/campaigns")
    assert response.status_code == 200
    assert next(item for item in response.json() if item["id"] == campaign_id)["status"] == "active"
    async with app.state.sessionmaker() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.status == "active"


async def test_campaign_list_error_keeps_saved_status(
    client: httpx.AsyncClient,
    app: FastAPI,
    respx_mock: respx.MockRouter,
    published_stream: tuple[int, int, int],
) -> None:
    campaign_id, _, _ = published_stream
    respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").respond(503)
    response = await client.get("/api/campaigns")
    assert response.status_code >= 500
    async with app.state.sessionmaker() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.status == "active"
