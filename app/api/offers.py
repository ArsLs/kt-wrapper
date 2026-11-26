"""Offer catalog search endpoint."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.db import SessionDep
from app.keitaro.client import KeitaroDep
from app.models import StreamOffer
from app.services.offer_catalog import OfferCatalog

router = APIRouter(prefix="/api", tags=["offers"])


class OfferSearchOut(BaseModel):
    id: int
    name: str
    status: str | None = None


@router.get("/offers/search")
async def search_offers(
    request: Request,
    keitaro: KeitaroDep,
    session: SessionDep,
    q: Annotated[str, Query()] = "",
    stream_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[OfferSearchOut]:
    query = q.strip()
    if not query:
        return []
    catalog: OfferCatalog = request.app.state.offer_catalog
    offers = await catalog.list(keitaro)
    needle = query.casefold()
    matches = [
        offer
        for offer in offers
        if offer.state == "active" and (str(offer.id) == query or needle in offer.name.casefold())
    ][:20]
    statuses: dict[int, str] = {}
    if stream_id is not None:
        rows = await session.scalars(select(StreamOffer).where(StreamOffer.stream_id == stream_id))
        statuses = {row.offer_id: row.status for row in rows}
    return [
        OfferSearchOut(id=offer.id, name=offer.name, status=statuses.get(offer.id))
        for offer in matches
    ]
