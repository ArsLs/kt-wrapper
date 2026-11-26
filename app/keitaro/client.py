"""Async Keitaro Admin API client."""

import logging
from decimal import Decimal
from typing import Annotated, Any, TypeVar

import httpx
from fastapi import Depends, Request
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config import Settings
from app.keitaro.errors import (
    KeitaroInvalidOfferWeightsError,
    KeitaroInvalidResponseError,
    KeitaroNotFoundError,
    KeitaroTimeoutError,
    KeitaroUnavailableError,
    KeitaroUpstreamError,
)
from app.keitaro.schemas import (
    INVALID_OFFER_WEIGHT,
    CampaignCreateIn,
    CampaignOut,
    CampaignUpdateIn,
    CreatedResourceOut,
    DomainOut,
    GroupOut,
    OfferOut,
    StreamCreateIn,
    StreamOut,
    StreamRecoveryOut,
    StreamUpdateIn,
    TrafficSourceOut,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    """Create a reusable, authenticated transport without logging credentials."""
    base_url = f"{str(settings.kt_admin_api_url).rstrip('/')}/"
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Api-Key": settings.kt_admin_api_key.get_secret_value()},
        timeout=httpx.Timeout(5.0, connect=2.0),
        follow_redirects=False,
    )


class KeitaroClient:
    """Typed operations used by the wrapper; each method performs one request."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        operation = f"{method} {path.rsplit('/', 1)[-1]}"
        try:
            response = await self._http.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            logger.warning("Keitaro request timed out (%s)", operation)
            raise KeitaroTimeoutError() from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("Keitaro returned HTTP %s (%s)", status, operation)
            if status == 404:
                raise KeitaroNotFoundError() from None
            raise KeitaroUpstreamError(status) from None
        except httpx.RequestError as exc:
            logger.warning("Keitaro transport failed (%s)", type(exc).__name__)
            raise KeitaroUnavailableError() from None

    async def _decode(self, response: httpx.Response, adapter: TypeAdapter[T]) -> T:
        try:
            return adapter.validate_python(response.json(parse_float=Decimal))
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            if any(error["type"] == INVALID_OFFER_WEIGHT for error in errors):
                raise KeitaroInvalidOfferWeightsError() from None
            logger.warning("Keitaro returned an invalid response")
            raise KeitaroInvalidResponseError() from None
        except ValueError:
            logger.warning("Keitaro returned an invalid response")
            raise KeitaroInvalidResponseError() from None

    async def _get(self, path: str, adapter: TypeAdapter[T], **kwargs: Any) -> T:
        return await self._decode(await self._request("GET", path, **kwargs), adapter)

    async def _create(self, path: str, payload: BaseModel) -> CreatedResourceOut:
        response = await self._request("POST", path, json=_dump(payload))
        return await self._decode(response, TypeAdapter(CreatedResourceOut))

    async def _update(self, path: str, payload: BaseModel) -> None:
        await self._request("PUT", path, json=_dump(payload))

    async def _delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def list_campaigns(self) -> list[CampaignOut]:
        return await self._get("campaigns", TypeAdapter(list[CampaignOut]))

    async def get_campaign(self, campaign_id: int) -> CampaignOut:
        return await self._get(f"campaigns/{campaign_id}", TypeAdapter(CampaignOut))

    async def create_campaign(self, campaign: CampaignCreateIn) -> CreatedResourceOut:
        return await self._create("campaigns", campaign)

    async def update_campaign(self, campaign_id: int, campaign: CampaignUpdateIn) -> None:
        await self._update(f"campaigns/{campaign_id}", campaign)

    async def delete_campaign(self, campaign_id: int) -> None:
        await self._delete(f"campaigns/{campaign_id}")

    async def list_streams(self) -> list[StreamOut]:
        return await self._get("streams", TypeAdapter(list[StreamOut]))

    async def get_stream(self, stream_id: int) -> StreamOut:
        return await self._get(f"streams/{stream_id}", TypeAdapter(StreamOut))

    async def get_stream_for_recovery(self, stream_id: int) -> StreamRecoveryOut:
        return await self._get(f"streams/{stream_id}", TypeAdapter(StreamRecoveryOut))

    async def create_stream(self, stream: StreamCreateIn) -> CreatedResourceOut:
        return await self._create("streams", stream)

    async def update_stream(self, stream_id: int, stream: StreamUpdateIn) -> None:
        await self._update(f"streams/{stream_id}", stream)

    async def delete_stream(self, stream_id: int) -> None:
        await self._delete(f"streams/{stream_id}")

    async def list_campaign_streams(self, campaign_id: int) -> list[StreamOut]:
        return await self._get(f"campaigns/{campaign_id}/streams", TypeAdapter(list[StreamOut]))

    async def list_offers(self) -> list[OfferOut]:
        return await self._get("offers", TypeAdapter(list[OfferOut]))

    async def list_domains(self) -> list[DomainOut]:
        return await self._get("domains", TypeAdapter(list[DomainOut]))

    async def list_groups(self, group_type: str = "campaigns") -> list[GroupOut]:
        return await self._get("groups", TypeAdapter(list[GroupOut]), params={"type": group_type})

    async def list_traffic_sources(self) -> list[TrafficSourceOut]:
        return await self._get("traffic_sources", TypeAdapter(list[TrafficSourceOut]))


def _dump(payload: BaseModel) -> dict[str, object]:
    return payload.model_dump(mode="json", by_alias=True, exclude_unset=True, exclude_none=True)


async def get_keitaro_client(request: Request) -> KeitaroClient:
    """Return the application-scoped Keitaro client."""
    client: KeitaroClient = request.app.state.keitaro_client
    return client


KeitaroDep = Annotated[KeitaroClient, Depends(get_keitaro_client)]
