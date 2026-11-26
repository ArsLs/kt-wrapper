"""FastAPI application factory: API routers under /api and the built SPA at /."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.api import campaign_create, editor, geo, health, offers
from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker
from app.errors import register_error_handlers
from app.keitaro.client import KeitaroClient, make_http_client
from app.request_logging import RequestLoggingMiddleware
from app.services.offer_catalog import OfferCatalog

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Serve the built frontend; unknown non-API paths fall back to index.html."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        try:
            async with make_http_client(settings) as http_client:
                app.state.keitaro_client = KeitaroClient(http_client)
                yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Keitaro Wrapper", lifespan=lifespan)
    app.add_middleware(RequestLoggingMiddleware)
    app.state.offer_catalog = OfferCatalog()
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(geo.router)
    app.include_router(offers.router)
    app.include_router(campaign_create.router)
    app.include_router(editor.router)

    if settings.frontend_dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True), name="spa")
    else:
        logger.warning("Frontend build not found at %s, serving API only", settings.frontend_dist)
    return app
