"""Create a standard campaign with its two initial streams."""

import logging

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from app.db import SessionDep
from app.keitaro.client import KeitaroDep
from app.logging_config import log_event
from app.services import idempotency
from app.services.campaign_create import create_campaign, create_idempotent
from app.services.idempotency import IdempotencyKey
from app.services.offer_catalog import OfferCatalog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["campaigns"])


class CampaignCreateBody(BaseModel):
    name: str
    geo: str = Field(min_length=2, max_length=2)
    offer_id: int = Field(strict=True, gt=0)


class CampaignCreatedOut(BaseModel):
    id: int
    keitaro_id: int
    name: str
    alias: str
    geo: str


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def post_campaign(
    body: CampaignCreateBody,
    request: Request,
    keitaro: KeitaroDep,
    session: SessionDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> CampaignCreatedOut:
    catalog: OfferCatalog = request.app.state.offer_catalog
    if idempotency_key is not None:
        result, replayed = await create_idempotent(
            keitaro,
            session,
            catalog,
            idempotency_key,
            name=body.name,
            geo=body.geo,
            offer_id=body.offer_id,
        )
        created = CampaignCreatedOut.model_validate(result)
        if replayed:
            response.headers[idempotency.REPLAY_HEADER] = "true"
            log_event(
                logger,
                logging.INFO,
                "campaign.create",
                stage="idempotency",
                outcome="replayed",
                campaign_id=created.id,
                keitaro_campaign_id=created.keitaro_id,
            )
        return created
    campaign, keitaro_id = await create_campaign(
        keitaro, session, catalog, name=body.name, geo=body.geo, offer_id=body.offer_id
    )
    return CampaignCreatedOut(
        id=campaign.id,
        keitaro_id=keitaro_id,
        name=campaign.name,
        alias=campaign.alias,
        geo=campaign.geo,
    )
