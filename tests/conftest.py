import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import AnyHttpUrl, SecretStr

from app.config import Settings
from app.keitaro.client import KeitaroClient, make_http_client
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://kt:kt@localhost:5432/kt_wrapper_test"
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        kt_admin_api_url=AnyHttpUrl("https://keitaro.test/admin_api/v1"),
        kt_admin_api_key=SecretStr("test-key"),
        frontend_dist=tmp_path / "dist",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def keitaro_client(
    settings: Settings, respx_mock: respx.MockRouter
) -> AsyncIterator[KeitaroClient]:
    """Provide a test-only Keitaro client with all network requests intercepted."""
    del respx_mock
    async with make_http_client(settings) as http:
        yield KeitaroClient(http)
