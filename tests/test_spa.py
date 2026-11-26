from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app


@pytest.fixture
async def spa_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    dist = settings.frontend_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    application = create_app(settings)
    async with LifespanManager(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def test_root_serves_index(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/")
    assert response.status_code == 200
    assert "<div id=root>" in response.text


async def test_asset_is_served(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/assets/app.js")
    assert response.status_code == 200


async def test_client_route_falls_back_to_index(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/campaigns/42")
    assert response.status_code == 200
    assert "<div id=root>" in response.text


async def test_unknown_api_path_is_json_404(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/api/nope")
    assert response.status_code == 404
    assert "<div id=root>" not in response.text


async def test_missing_dist_serves_api_only(client: httpx.AsyncClient, tmp_path: Path) -> None:
    response = await client.get("/")
    assert response.status_code == 404
