"""Correlate application events and log HTTP responses using route templates."""

import logging
from asyncio import CancelledError
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import log_event, request_id

logger = logging.getLogger("app.http")
REQUEST_ID_HEADER = "X-Request-ID"


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        correlation_id = uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = correlation_id
        token = request_id.set(correlation_id)
        status_code: int | None = None
        error: BaseException | None = None

        async def send_response(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_response)
        except (Exception, CancelledError) as exc:
            error = exc
            if isinstance(exc, Exception) and status_code is None:
                status_code = 500
            raise
        finally:
            try:
                route = getattr(scope.get("route"), "path_format", None)
                level = logging.INFO
                if isinstance(error, Exception) or (status_code is not None and status_code >= 500):
                    level = logging.ERROR
                elif route == "/api/health" and status_code == 200 and error is None:
                    level = logging.DEBUG
                log_event(
                    logger,
                    level,
                    "http.request",
                    method=scope["method"],
                    route=route,
                    status_code=status_code,
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    error_code=state.get("error_code"),
                    exc_info=error,
                )
            finally:
                request_id.reset(token)
