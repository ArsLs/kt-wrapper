"""Contract tests using synthetic Keitaro responses; no emulator is contacted."""

import json
import logging

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from pydantic import AnyHttpUrl, SecretStr

from app.config import Settings
from app.keitaro.client import KeitaroClient, make_http_client
from app.keitaro.errors import (
    KeitaroInvalidResponseError,
    KeitaroNotFoundError,
    KeitaroTimeoutError,
    KeitaroUnavailableError,
    KeitaroUpstreamError,
)
from app.keitaro.schemas import CampaignCreateIn, StreamUpdateIn
from app.main import create_app


@pytest.mark.parametrize(
    ("method_name", "path", "payload"),
    [
        ("list_offers", "offers", [{"id": 1, "name": "Synthetic", "state": "active"}]),
        ("list_domains", "domains", [{"id": 2, "name": "domain.test"}]),
        ("list_groups", "groups", [{"id": 3, "name": "FORTESTS"}]),
        (
            "list_traffic_sources",
            "traffic_sources",
            [{"id": 4, "name": "FORTESTS"}],
        ),
    ],
)
async def test_catalog_methods_use_expected_routes(
    keitaro_client: KeitaroClient,
    respx_mock: respx.MockRouter,
    method_name: str,
    path: str,
    payload: list[dict[str, object]],
) -> None:
    route = respx_mock.get(f"https://keitaro.test/admin_api/v1/{path}").mock(
        return_value=httpx.Response(200, json=payload)
    )

    result = await getattr(keitaro_client, method_name)()

    assert len(result) == 1
    assert route.call_count == 1
    request = respx_mock.calls.last.request
    assert request.headers["Api-Key"] == "test-key"
    if path == "groups":
        assert request.url.params["type"] == "campaigns"


async def test_campaign_create_serializes_only_configured_fields(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("https://keitaro.test/admin_api/v1/campaigns").mock(
        return_value=httpx.Response(201, json={"id": 71, "ignored": "extra"})
    )

    created = await keitaro_client.create_campaign(
        CampaignCreateIn(
            name="Demo",
            alias="demo",
            type="web",
            domain_id=2,
            group_id=3,
            traffic_source_id=4,
            cost_type="CPC",
            cost_currency="USD",
            cost_auto=True,
        )
    )

    assert created.id == 71
    assert route.call_count == 1
    assert json.loads(respx_mock.calls.last.request.content) == {
        "name": "Demo",
        "alias": "demo",
        "type": "web",
        "domain_id": 2,
        "group_id": 3,
        "traffic_source_id": 4,
        "cost_type": "CPC",
        "cost_currency": "USD",
        "cost_auto": True,
    }


async def test_empty_offers_array_is_preserved_by_stream_update(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.put("https://keitaro.test/admin_api/v1/streams/12").mock(
        return_value=httpx.Response(204)
    )

    await keitaro_client.update_stream(12, StreamUpdateIn(offers=[]))

    assert route.call_count == 1
    assert respx_mock.calls.last.request.content == b'{"offers":[]}'


async def test_stream_read_defaults_missing_offers_to_empty(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://keitaro.test/admin_api/v1/campaigns/7/streams").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 12, "name": "Redirect", "campaign_id": 7, "position": 0}],
        )
    )

    streams = await keitaro_client.list_campaign_streams(7)

    assert streams[0].offers == []
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (302, KeitaroUpstreamError),
        (400, KeitaroUpstreamError),
        (401, KeitaroUpstreamError),
        (403, KeitaroUpstreamError),
        (404, KeitaroNotFoundError),
        (429, KeitaroUpstreamError),
        (500, KeitaroUpstreamError),
    ],
)
async def test_http_errors_are_mapped_without_retry(
    keitaro_client: KeitaroClient,
    respx_mock: respx.MockRouter,
    status: int,
    error: type[Exception],
) -> None:
    route = respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").mock(
        return_value=httpx.Response(status, text="synthetic secret response")
    )

    with pytest.raises(error) as raised:
        await keitaro_client.list_campaigns()

    assert route.call_count == 1
    if status != 404:
        assert isinstance(raised.value, KeitaroUpstreamError)
        assert raised.value.upstream_status == status
    assert "synthetic secret response" not in str(raised.value)


async def test_timeout_and_transport_errors_are_mapped_without_retry(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    timeout = respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").mock(
        side_effect=httpx.ReadTimeout("secret detail")
    )
    with pytest.raises(KeitaroTimeoutError):
        await keitaro_client.list_campaigns()
    assert timeout.call_count == 1


async def test_invalid_json_response_is_safe_and_logged(
    keitaro_client: KeitaroClient,
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").mock(
        return_value=httpx.Response(200, json={"unexpected": "secret"})
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(KeitaroInvalidResponseError) as raised:
        await keitaro_client.list_campaigns()

    assert "secret" not in str(raised.value)
    assert "secret" not in caplog.text


async def test_client_strips_trailing_slashes_and_does_not_follow_redirects() -> None:
    settings = Settings(
        _env_file=None,
        kt_admin_api_url=AnyHttpUrl("https://keitaro.test/admin_api/v1///"),
        kt_admin_api_key=SecretStr("test-key"),
    )
    async with make_http_client(settings) as http:
        assert str(http.base_url) == "https://keitaro.test/admin_api/v1/"
        assert http.follow_redirects is False


async def test_connect_error_maps_to_unavailable(
    keitaro_client: KeitaroClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").mock(
        side_effect=httpx.ConnectError("synthetic")
    )

    with pytest.raises(KeitaroUnavailableError):
        await keitaro_client.list_campaigns()

    assert route.call_count == 1


async def test_application_lifespan_owns_keitaro_client(
    settings: Settings, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get("https://keitaro.test/admin_api/v1/campaigns").mock(
        return_value=httpx.Response(200, json=[])
    )
    application = create_app(settings)

    async with LifespanManager(application):
        client = application.state.keitaro_client
        assert not client._http.is_closed
        assert await client.list_campaigns() == []
        assert route.call_count == 1

    assert client._http.is_closed
