"""Synthetic detail, list and mutation contracts for campaigns and streams."""

import json

import respx

from app.keitaro.client import KeitaroClient
from app.keitaro.schemas import CampaignUpdateIn, StreamCreateIn, StreamOfferIn

BASE = "https://keitaro.test/admin_api/v1"


async def test_read_update_and_delete_campaign(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    campaign = {"id": 5, "name": "Campaign", "state": "future_state"}
    respx_mock.get(f"{BASE}/campaigns").respond(200, json=[campaign])
    respx_mock.get(f"{BASE}/campaigns/5").respond(200, json=campaign)
    update = respx_mock.put(f"{BASE}/campaigns/5").respond(200, text="ok")
    delete = respx_mock.delete(f"{BASE}/campaigns/5").respond(204)

    summary = await keitaro_client.list_campaigns()
    detail = await keitaro_client.get_campaign(5)
    assert summary == [detail]
    assert detail.state == "future_state"
    await keitaro_client.update_campaign(5, CampaignUpdateIn(name="Renamed"))
    assert json.loads(update.calls.last.request.content) == {"name": "Renamed"}
    await keitaro_client.delete_campaign(5)
    assert delete.called
    assert respx_mock.calls.call_count == 4


async def test_read_create_and_delete_stream(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    offer = {"offer_id": 9, "share": 100, "state": "active"}
    payload = {
        "campaign_id": 5,
        "name": "Offers",
        "position": 2,
        "schema": "landings",
        "type": "regular",
        "action_type": "http",
        "offers": [offer],
        "landings": [],
    }
    stream = {"id": 8, **payload}
    respx_mock.get(f"{BASE}/streams").respond(200, json=[stream])
    respx_mock.get(f"{BASE}/streams/8").respond(200, json=stream)
    create = respx_mock.post(f"{BASE}/streams").respond(201, json={"id": 8})
    delete = respx_mock.delete(f"{BASE}/streams/8").respond(204)

    streams = await keitaro_client.list_streams()
    detail = await keitaro_client.get_stream(8)
    assert streams == [detail]
    assert detail.offers[0].name is None
    created = await keitaro_client.create_stream(
        StreamCreateIn(
            campaign_id=5,
            name="Offers",
            position=2,
            schema="landings",
            type="regular",
            action_type="http",
            offers=[StreamOfferIn(offer_id=9, share=100, state="active")],
            landings=[],
        )
    )
    assert created.id == 8
    assert json.loads(create.calls.last.request.content) == payload
    await keitaro_client.delete_stream(8)
    assert delete.called
    assert respx_mock.calls.call_count == 4
