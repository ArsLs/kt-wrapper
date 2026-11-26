import httpx
import respx

BASE = "https://keitaro.test/admin_api/v1"


async def test_offer_search_filters_active_and_uses_cache(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{BASE}/offers").respond(
        200,
        json=[
            {"id": 12, "name": "Alpha offer", "state": "active"},
            {"id": 13, "name": "Inactive alpha", "state": "paused"},
            {"id": 14, "name": "Other", "state": "active"},
        ],
    )
    by_name = await client.get("/api/offers/search", params={"q": " ALPHA "})
    by_id = await client.get("/api/offers/search", params={"q": "12"})
    assert by_name.status_code == 200
    assert [item["id"] for item in by_name.json()] == [12]
    assert [item["id"] for item in by_id.json()] == [12]
    assert route.call_count == 1


async def test_empty_offer_search_does_not_call_keitaro(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{BASE}/offers")
    response = await client.get("/api/offers/search", params={"q": "  "})
    assert response.status_code == 200
    assert response.json() == []
    assert route.call_count == 0
