import asyncio
import json
import logging
from collections.abc import Iterator
from io import StringIO

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError

from app.logging_config import JsonFormatter, log_event, request_id


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[StringIO]:
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app")
    monkeypatch.setattr(logger, "level", logging.INFO)
    logger.addHandler(handler)
    try:
        yield output
    finally:
        logger.removeHandler(handler)
        handler.close()


async def test_request_logs_correlate_concurrent_requests_and_hide_inputs(
    app: FastAPI, client: httpx.AsyncClient, logs: StringIO
) -> None:
    @app.get("/api/test/{item_id}")
    async def detail(item_id: int) -> int:
        await asyncio.sleep(0)
        log_event(logging.getLogger("app.test"), logging.INFO, "test.detail", item_id=item_id)
        return item_id

    results = await asyncio.gather(
        client.get("/api/test/1?q=synthetic-query", headers={"X-Request-ID": "synthetic-header"}),
        client.get("/api/test/2"),
        client.post("/api/streams/123/push", json={}),
        client.get("/api/synthetic-path"),
    )

    assert [response.status_code for response in results] == [200, 200, 422, 404]
    ids = [response.headers["X-Request-ID"] for response in results]
    assert len(set(ids)) == 4
    assert request_id.get() is None
    assert "synthetic-" not in logs.getvalue()
    entries = [json.loads(line) for line in logs.getvalue().splitlines()]
    http_logs = {
        entry["request_id"]: entry for entry in entries if entry["event"] == "http.request"
    }
    detail_logs = {entry["item_id"]: entry for entry in entries if entry["event"] == "test.detail"}
    assert detail_logs[1]["request_id"] == ids[0]
    assert detail_logs[2]["request_id"] == ids[1]
    assert http_logs[ids[0]]["route"] == "/api/test/{item_id}"
    assert http_logs[ids[2]]["route"] == "/api/streams/{stream_id}/push"
    assert http_logs[ids[2]]["status_code"] == 422
    assert http_logs[ids[2]]["duration_ms"] >= 0
    assert http_logs[ids[3]]["route"] is None


async def test_unexpected_database_error_keeps_request_id_and_hides_details(
    app: FastAPI, logs: StringIO
) -> None:
    @app.get("/api/test/error")
    async def fail() -> None:
        raise IntegrityError(
            "UPDATE synthetic-sql SET name = $1",
            ("synthetic-parameter",),
            ValueError("synthetic-driver-detail"),
        )

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/test/error")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert "synthetic-" not in logs.getvalue()
    entry = json.loads(logs.getvalue())
    assert entry["request_id"] == response.headers["X-Request-ID"]
    assert entry["error_type"] == "IntegrityError"
    assert entry["level"] == "ERROR"
    assert entry["status_code"] == 500
    assert any("test_logging.fail:" in frame for frame in entry["stack"])
