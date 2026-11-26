"""HTTP API for the published offer editor state."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request, Response, status
from pydantic import BaseModel, ConfigDict, StrictInt

from app.db import SessionDep
from app.keitaro.client import KeitaroDep
from app.logging_config import log_event
from app.services import editor, idempotency
from app.services.idempotency import IdempotencyKey

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["editor"])


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OfferIntent(InputModel):
    offer_id: StrictInt
    status: Literal["active", "added", "removed"]


class SnapshotIn(InputModel):
    offers: list[OfferIntent]
    unpin_offer_ids: list[StrictInt] = []


class PinIn(InputModel):
    pinned: bool


class OfferOut(BaseModel):
    offer_id: int
    offer_name: str
    position: int
    status: str
    weight: int
    pinned: bool
    can_pin: bool
    can_remove: bool


class SnapshotOut(BaseModel):
    offers: list[OfferOut]
    unpin_offer_ids: list[int]


class StreamOut(BaseModel):
    id: int
    keitaro_id: int
    name: str
    position: int
    has_offers: bool
    status: str
    offers: list[OfferOut]


class CampaignOut(BaseModel):
    id: int
    keitaro_id: int
    name: str
    alias: str
    geo: str
    status: str


def _desired(payload: SnapshotIn) -> list[editor.DesiredOffer]:
    return [editor.DesiredOffer(item.offer_id, item.status) for item in payload.offers]


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns(session: SessionDep, keitaro: KeitaroDep) -> list[editor.CampaignOut]:
    return await editor.campaigns(session, keitaro)


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_campaign(campaign_id: int, session: SessionDep, keitaro: KeitaroDep) -> Response:
    await editor.delete_campaign(session, keitaro, campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/campaigns/{campaign_id}/fetch", response_model=list[StreamOut])
async def fetch_streams(
    campaign_id: int, request: Request, session: SessionDep, keitaro: KeitaroDep
) -> list[editor.StreamOutDict]:
    return await editor.fetch_streams(
        session, keitaro, campaign_id, request.app.state.offer_catalog
    )


@router.get("/campaigns/{campaign_id}/streams", response_model=list[StreamOut])
async def list_streams(campaign_id: int, session: SessionDep) -> list[editor.StreamOutDict]:
    return await editor.get_streams(session, campaign_id)


@router.post("/streams/{stream_id}/preview", response_model=SnapshotOut)
async def preview(
    stream_id: int,
    payload: SnapshotIn,
    request: Request,
    session: SessionDep,
    keitaro: KeitaroDep,
) -> editor.SnapshotEnvelope:
    return await editor.preview_snapshot(
        session,
        keitaro,
        request.app.state.offer_catalog,
        stream_id,
        _desired(payload),
        payload.unpin_offer_ids,
    )


@router.post("/streams/{stream_id}/push", response_model=StreamOut)
async def push(
    stream_id: int,
    payload: SnapshotIn,
    request: Request,
    session: SessionDep,
    keitaro: KeitaroDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    replay_only: Annotated[Literal["true"] | None, Header(alias="Idempotency-Replay-Only")] = None,
) -> editor.StreamOutDict | dict[str, object]:
    operation = None
    if idempotency_key is not None:
        body = {"stream_id": stream_id, **payload.model_dump(mode="json")}
        if replay_only is None:
            await idempotency.reserve(session, idempotency_key, "push", body)
        operation = await idempotency.load(session, idempotency_key, "push", body)
        if operation.result is not None:
            response.headers[idempotency.REPLAY_HEADER] = "true"
            log_event(
                logger,
                logging.INFO,
                "stream.push",
                stage="idempotency",
                outcome="replayed",
                stream_id=stream_id,
            )
            return operation.result
    if replay_only is not None:
        raise idempotency.IdempotencyResultUnavailableError()
    return await editor.push_snapshot(
        session,
        keitaro,
        request.app.state.offer_catalog,
        stream_id,
        _desired(payload),
        payload.unpin_offer_ids,
        operation=operation,
    )


@router.put("/streams/{stream_id}/offers/{offer_id}/pin", response_model=StreamOut)
async def set_pin(
    stream_id: int,
    offer_id: int,
    payload: PinIn,
    session: SessionDep,
    x_stream_dirty: Annotated[Literal["true", "false"], Header(alias="X-Stream-Dirty")],
) -> editor.StreamOutDict:
    return await editor.pin_offer(
        session, stream_id, offer_id, payload.pinned, x_stream_dirty == "true"
    )
