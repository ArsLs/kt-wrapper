import httpx
from asgi_lifespan import LifespanManager

from app.config import Settings
from app.main import create_app


async def test_health_reports_ok_when_database_is_reachable(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_returns_503_when_database_is_down(settings: Settings) -> None:
    broken = settings.model_copy(
        update={"database_url": "postgresql+asyncpg://kt:kt@127.0.0.1:1/nowhere"}
    )
    application = create_app(broken)
    async with LifespanManager(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
