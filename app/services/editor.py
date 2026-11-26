"""Published editor state and its synchronization with Keitaro."""

import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.editor_errors import (
    EditorConflictError,
    EditorNotFoundError,
    EditorPersistenceError,
    EditorValidationError,
)
from app.domain.weights import (
    OfferAction,
    OfferWeight,
    WeightRuleError,
    apply_action,
    recalculate,
    validate_distribution,
)
from app.domain.weights import (
    can_pin as domain_can_pin,
)
from app.domain.weights import (
    can_remove as domain_can_remove,
)
from app.keitaro.client import KeitaroClient
from app.keitaro.errors import KeitaroError, KeitaroNotFoundError
from app.keitaro.schemas import StreamOfferIn, StreamOut, StreamUpdateIn
from app.logging_config import log_event
from app.models import Campaign, MutationOperation, Stream, StreamOffer
from app.services.offer_catalog import OfferCatalog

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesiredOffer:
    offer_id: int
    status: str


@dataclass(frozen=True)
class SnapshotOffer:
    offer_id: int
    offer_name: str
    position: int
    status: str
    weight: int
    pinned: bool
    can_pin: bool
    can_remove: bool


class CampaignOut(TypedDict):
    id: int
    keitaro_id: int
    name: str
    alias: str
    geo: str
    status: str


class StreamOutDict(TypedDict):
    id: int
    keitaro_id: int
    name: str
    position: int
    has_offers: bool
    status: str
    offers: list[dict[str, object]]


class SnapshotOut(TypedDict):
    offer_id: int
    offer_name: str
    position: int
    status: str
    weight: int
    pinned: bool
    can_pin: bool
    can_remove: bool


class SnapshotEnvelope(TypedDict):
    offers: list[SnapshotOut]
    unpin_offer_ids: list[int]


def _domain(rows: Sequence[StreamOffer]) -> list[OfferWeight]:
    return [
        OfferWeight(
            offer_id=row.offer_id,
            position=row.position,
            status=row.status,  # type: ignore[arg-type]
            weight=row.weight,
            pinned=row.pinned,
        )
        for row in rows
    ]


def _rule_error(exc: WeightRuleError) -> EditorValidationError:
    return EditorValidationError(str(exc))


async def get_campaign(session: AsyncSession, campaign_id: int) -> Campaign:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.deleted_at.is_(None))
    )
    if campaign is None:
        raise EditorNotFoundError("Campaign was not found")
    return campaign


async def get_stream(session: AsyncSession, stream_id: int, *, lock: bool = False) -> Stream:
    query = select(Stream).where(Stream.id == stream_id)
    if lock:
        query = query.with_for_update()
    stream = await session.scalar(query)
    if stream is None:
        raise EditorNotFoundError("Stream was not found")
    if stream.status != "published":
        raise EditorConflictError("Stream is removed")
    await get_campaign(session, stream.campaign_id)
    return stream


async def lock_stream(session: AsyncSession, stream_id: int) -> Stream:
    campaign_id = await session.scalar(select(Stream.campaign_id).where(Stream.id == stream_id))
    if campaign_id is None:
        raise EditorNotFoundError("Stream was not found")
    campaign = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.deleted_at.is_(None))
        .with_for_update()
    )
    if campaign is None:
        raise EditorNotFoundError("Campaign was not found")
    return await get_stream(session, stream_id, lock=True)


async def stream_rows(session: AsyncSession, stream_id: int) -> list[StreamOffer]:
    result = await session.scalars(
        select(StreamOffer)
        .where(StreamOffer.stream_id == stream_id)
        .order_by(StreamOffer.position, StreamOffer.offer_id)
    )
    return list(result)


def build_response(rows: Sequence[StreamOffer]) -> list[SnapshotOffer]:
    offers = _domain(rows)
    try:
        capability_snapshot = recalculate(offers)
    except WeightRuleError:
        capability_snapshot = offers
    result: list[SnapshotOffer] = []
    ordered_offers = sorted(offers, key=lambda item: (item.position, item.offer_id))
    ordered_rows = sorted(rows, key=lambda item: (item.position, item.offer_id))
    for offer, row in zip(ordered_offers, ordered_rows, strict=True):
        can_pin = False
        can_remove = False
        with suppress(WeightRuleError):
            can_pin = domain_can_pin(offers, offer.offer_id)
        with suppress(WeightRuleError):
            can_remove = domain_can_remove(capability_snapshot, offer.offer_id)
        result.append(
            SnapshotOffer(
                offer_id=row.offer_id,
                offer_name=row.offer_name,
                position=row.position,
                status=row.status,
                weight=row.weight,
                pinned=row.pinned,
                can_pin=can_pin,
                can_remove=can_remove,
            )
        )
    return result


async def campaigns(session: AsyncSession, keitaro: KeitaroClient) -> list[CampaignOut]:
    remote = await keitaro.list_campaigns()
    remote_ids = {campaign.id for campaign in remote}
    local = list(
        await session.scalars(
            select(Campaign).where(Campaign.deleted_at.is_(None)).order_by(Campaign.id)
        )
    )
    changed = False
    for campaign in local:
        status = "active" if campaign.keitaro_id in remote_ids else "removed"
        if campaign.status != status:
            campaign.status = status
            changed = True
    if changed:
        await session.commit()
    return [
        {
            "id": campaign.id,
            "keitaro_id": campaign.keitaro_id,
            "name": campaign.name,
            "alias": campaign.alias,
            "geo": campaign.geo,
            "status": campaign.status,
        }
        for campaign in local
    ]


async def get_streams(session: AsyncSession, campaign_id: int) -> list[StreamOutDict]:
    await get_campaign(session, campaign_id)
    result = await session.execute(
        select(Stream, StreamOffer)
        .outerjoin(StreamOffer, StreamOffer.stream_id == Stream.id)
        .where(Stream.campaign_id == campaign_id)
        .order_by(Stream.position, Stream.id, StreamOffer.position, StreamOffer.offer_id)
    )
    streams_by_id: dict[int, tuple[Stream, list[StreamOffer]]] = {}
    for stream, offer in result:
        entry = streams_by_id.setdefault(stream.id, (stream, []))
        if offer is not None:
            entry[1].append(offer)
    return [
        {
            "id": stream.id,
            "keitaro_id": stream.keitaro_id,
            "name": stream.name,
            "position": stream.position,
            "has_offers": stream.has_offers,
            "status": stream.status,
            "offers": [
                {
                    "offer_id": offer.offer_id,
                    "offer_name": offer.offer_name,
                    "position": offer.position,
                    "status": offer.status,
                    "weight": offer.weight,
                    "pinned": offer.pinned,
                    "can_pin": offer.can_pin,
                    "can_remove": offer.can_remove,
                }
                for offer in build_response(offers)
            ]
            if stream.has_offers
            else [],
        }
        for stream, offers in streams_by_id.values()
    ]


async def get_stream_snapshot(session: AsyncSession, stream_id: int) -> StreamOutDict:
    stream = await get_stream(session, stream_id)
    rows = await stream_rows(session, stream_id)
    return {
        "id": stream.id,
        "keitaro_id": stream.keitaro_id,
        "name": stream.name,
        "position": stream.position,
        "has_offers": stream.has_offers,
        "status": stream.status,
        "offers": [
            {
                "offer_id": offer.offer_id,
                "offer_name": offer.offer_name,
                "position": offer.position,
                "status": offer.status,
                "weight": offer.weight,
                "pinned": offer.pinned,
                "can_pin": offer.can_pin,
                "can_remove": offer.can_remove,
            }
            for offer in build_response(rows)
        ]
        if stream.has_offers
        else [],
    }


async def preview_snapshot(
    session: AsyncSession,
    keitaro: KeitaroClient,
    catalog: OfferCatalog,
    stream_id: int,
    desired: Sequence[DesiredOffer],
    unpin_offer_ids: Sequence[int],
) -> SnapshotEnvelope:
    stream = await get_stream(session, stream_id)
    if not stream.has_offers:
        raise EditorConflictError("Stream does not contain offers")
    loaded = await stream_rows(session, stream_id)
    current = [
        StreamOffer(
            stream_id=row.stream_id,
            offer_id=row.offer_id,
            offer_name=row.offer_name,
            status=row.status,
            weight=row.weight,
            pinned=row.pinned,
            position=row.position,
        )
        for row in loaded
    ]
    current_by_id = {row.offer_id: row for row in current}
    requested: dict[int, DesiredOffer] = {}
    for item in desired:
        if isinstance(item.offer_id, bool) or item.offer_id <= 0:
            raise EditorValidationError("offer_id must be a positive integer")
        if item.offer_id in requested:
            raise EditorValidationError("offer_id values must be unique")
        requested[item.offer_id] = item
    if set(current_by_id) - set(requested):
        raise EditorValidationError("Published offers cannot be omitted")
    pinned_ids = {row.offer_id for row in current if row.pinned}
    if len(set(unpin_offer_ids)) != len(unpin_offer_ids) or not set(unpin_offer_ids) <= pinned_ids:
        raise EditorValidationError("unpin_offer_ids must identify published pinned offers")

    values = {row.offer_id: row for row in current}
    next_position = max((row.position for row in current), default=-1) + 1
    for offer_id, item in requested.items():
        row = current_by_id.get(offer_id)
        if row is None:
            if item.status != "added":
                raise EditorValidationError("New offers must have status added")
            found = await catalog.get_active(keitaro, offer_id)
            if found is None or found.state != "active":
                raise EditorValidationError("Offer is unavailable")
            values[offer_id] = StreamOffer(
                stream_id=stream_id,
                offer_id=offer_id,
                offer_name=found.name,
                status="added",
                weight=0,
                pinned=False,
                position=next_position,
            )
            next_position += 1
        elif row.status == "removed":
            if item.status == "removed":
                continue
            if item.status != "active":
                raise EditorValidationError("Archived offers can only be brought back as active")
            row.status = "active"
            row.pinned = False
        elif item.status == "removed":
            row.status = "removed"
            row.weight = 0
            row.pinned = False
        elif item.status not in ("active", "added"):
            raise EditorValidationError("Unknown offer status")
        elif row.status == "added" and item.status != "added":
            raise EditorValidationError("New offers must remain added until Push")
        elif row.status == "active" and item.status != "active":
            raise EditorValidationError("Invalid offer transition")

    for offer_id in unpin_offer_ids:
        if offer_id in requested and requested[offer_id].status == "active":
            values[offer_id].pinned = False

    try:
        normalized = recalculate(_domain(list(values.values())))
        validate_distribution(normalized)
    except WeightRuleError as exc:
        raise _rule_error(exc) from exc
    by_id = {item.offer_id: item for item in normalized}
    output: list[SnapshotOut] = []
    for row in sorted(values.values(), key=lambda item: (item.position, item.offer_id)):
        domain = by_id[row.offer_id]
        row.status, row.weight, row.pinned = domain.status, domain.weight, domain.pinned
        can_pin = can_remove = False
        try:
            can_pin = domain_can_pin(normalized, row.offer_id)
            can_remove = domain_can_remove(normalized, row.offer_id)
        except WeightRuleError:
            pass
        output.append(
            {
                "offer_id": row.offer_id,
                "offer_name": row.offer_name,
                "position": row.position,
                "status": row.status,
                "weight": row.weight,
                "pinned": row.pinned,
                "can_pin": can_pin,
                "can_remove": can_remove,
            }
        )
    return {"offers": output, "unpin_offer_ids": sorted(unpin_offer_ids)}


async def delete_campaign(session: AsyncSession, keitaro: KeitaroClient, campaign_id: int) -> None:
    campaign = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.deleted_at.is_(None))
        .with_for_update()
    )
    if campaign is None:
        raise EditorNotFoundError("Campaign was not found")
    if campaign.status == "active":
        with suppress(KeitaroNotFoundError):
            await keitaro.delete_campaign(campaign.keitaro_id)
    campaign.deleted_at = datetime.now(UTC)
    await session.commit()


async def fetch_streams(
    session: AsyncSession,
    keitaro: KeitaroClient,
    campaign_id: int,
    catalog: OfferCatalog,
) -> list[StreamOutDict]:
    campaign = await session.scalar(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.deleted_at.is_(None))
        .with_for_update()
    )
    if campaign is None:
        raise EditorNotFoundError("Campaign was not found")
    local_streams = list(
        await session.scalars(
            select(Stream)
            .where(Stream.campaign_id == campaign_id)
            .order_by(Stream.id)
            .with_for_update()
        )
    )
    remote_streams = await keitaro.list_campaign_streams(campaign.keitaro_id)
    remote_by_id: dict[int, StreamOut] = {}
    for remote in remote_streams:
        if remote.campaign_id != campaign.keitaro_id:
            raise EditorValidationError("Keitaro returned a stream from another campaign")
        if remote.id in remote_by_id:
            raise EditorValidationError("Keitaro returned duplicate stream IDs")
        remote_by_id[remote.id] = remote
    local_by_id = {stream.keitaro_id: stream for stream in local_streams}
    cached_offers: dict[int, str] = {}
    cached_batch = catalog.cached()
    if cached_batch is not None:
        cached_offers = {offer.id: offer.name for offer in cached_batch}
    for stream in local_streams:
        if stream.keitaro_id not in remote_by_id:
            stream.status = "removed"
    for remote in remote_streams:
        matched_stream = local_by_id.get(remote.id)
        if matched_stream is None:
            matched_stream = Stream(
                campaign_id=campaign_id,
                keitaro_id=remote.id,
                name=remote.name,
                position=remote.position,
                has_offers=remote.schema_ == "landings",
                status="published",
            )
            session.add(matched_stream)
            await session.flush()
            local_by_id[remote.id] = matched_stream
        matched_stream.name = remote.name
        matched_stream.position = remote.position
        matched_stream.status = "published"
        matched_stream.has_offers = remote.schema_ == "landings"
        if not matched_stream.has_offers:
            for row in await stream_rows(session, matched_stream.id):
                row.status = "removed"
                row.weight = 0
                row.pinned = False
            continue
        rows = await stream_rows(session, matched_stream.id)
        by_offer_id = {row.offer_id: row for row in rows}
        seen: set[int] = set()
        for offer in remote.offers:
            if offer.offer_id in seen:
                raise EditorValidationError("Keitaro returned duplicate offer IDs")
            seen.add(offer.offer_id)
            existing_offer = by_offer_id.get(offer.offer_id)
            remote_active = offer.state == "active"
            if existing_offer is None:
                row = StreamOffer(
                    stream_id=matched_stream.id,
                    offer_id=offer.offer_id,
                    offer_name=offer.name
                    or cached_offers.get(offer.offer_id, f"Offer #{offer.offer_id}"),
                    position=len(by_offer_id),
                    status="active" if remote_active else "removed",
                    weight=offer.share if remote_active else 0,
                    pinned=False,
                )
                session.add(row)
                by_offer_id[offer.offer_id] = row
            else:
                row = existing_offer
                was_removed = row.status == "removed"
                row.status = "active" if remote_active else "removed"
                row.weight = offer.share if remote_active else 0
                if not remote_active:
                    row.pinned = False
                if was_removed:
                    row.pinned = False
                row.offer_name = offer.name or cached_offers.get(offer.offer_id, row.offer_name)
            row.position = len(seen) - 1
        for offer_id, row in by_offer_id.items():
            if offer_id not in seen:
                row.status = "removed"
                row.weight = 0
                row.pinned = False
        active = [row for row in by_offer_id.values() if row.status == "active"]
        while active and (
            not any(not row.pinned for row in active)
            or sum(row.weight for row in active if row.pinned) >= 100
        ):
            pinned = sorted(
                (row for row in active if row.pinned),
                key=lambda row: (row.position, row.offer_id),
                reverse=True,
            )
            if not pinned:
                break
            pinned[0].pinned = False
            logger.warning("Cleared invalid pin on offer %s during Fetch", pinned[0].offer_id)
    await session.commit()
    return await get_streams(session, campaign_id)


async def push_snapshot(
    session: AsyncSession,
    keitaro: KeitaroClient,
    catalog: OfferCatalog,
    stream_id: int,
    desired: Sequence[DesiredOffer],
    unpin_offer_ids: Sequence[int],
    *,
    operation: MutationOperation | None = None,
) -> StreamOutDict:
    stream = await lock_stream(session, stream_id)
    target = await preview_snapshot(session, keitaro, catalog, stream_id, desired, unpin_offer_ids)
    outgoing = [
        StreamOfferIn(offer_id=int(item["offer_id"]), share=int(item["weight"]), state="active")
        for item in target["offers"]
        if item["status"] in ("active", "added")
    ]
    keitaro_stream_id = stream.keitaro_id
    try:
        await keitaro.update_stream(keitaro_stream_id, StreamUpdateIn(offers=outgoing))
    except KeitaroError as exc:
        log_event(
            logger,
            logging.WARNING,
            "stream.push",
            stage="keitaro_update",
            outcome="failed",
            stream_id=stream_id,
            keitaro_stream_id=keitaro_stream_id,
            error_code=exc.code,
        )
        raise
    log_event(
        logger,
        logging.INFO,
        "stream.push",
        stage="keitaro_update",
        outcome="succeeded",
        stream_id=stream_id,
        keitaro_stream_id=keitaro_stream_id,
    )
    try:
        rows = {row.offer_id: row for row in await stream_rows(session, stream_id)}
        for item in target["offers"]:
            offer_id = int(item["offer_id"])
            row = rows.get(offer_id)
            if row is None:
                row = StreamOffer(
                    stream_id=stream_id,
                    offer_id=offer_id,
                    offer_name=str(item["offer_name"]),
                    position=int(item["position"]),
                    status="active",
                    weight=int(item["weight"]),
                    pinned=bool(item["pinned"]),
                )
                session.add(row)
            else:
                row.status = "active" if item["status"] == "added" else str(item["status"])
                row.weight = int(item["weight"])
                row.pinned = bool(item["pinned"])
                row.position = int(item["position"])
                row.offer_name = str(item["offer_name"])
        result = await get_stream_snapshot(session, stream_id)
        if operation is not None:
            operation.result = dict(result)
        await session.commit()
    except SQLAlchemyError as exc:
        log_event(
            logger,
            logging.ERROR,
            "stream.push",
            stage="persist",
            outcome="failed",
            stream_id=stream_id,
            keitaro_stream_id=keitaro_stream_id,
            exc_info=exc,
        )
        await session.rollback()
        raise EditorPersistenceError(
            "Keitaro was updated, but local state could not be saved. Fetch to reconcile."
        ) from None
    log_event(
        logger,
        logging.INFO,
        "stream.push",
        stage="persist",
        outcome="succeeded",
        stream_id=stream_id,
        keitaro_stream_id=keitaro_stream_id,
    )
    return result


async def pin_offer(
    session: AsyncSession,
    stream_id: int,
    offer_id: int,
    pinned: bool,
    dirty: bool,
) -> StreamOutDict:
    if dirty:
        raise EditorConflictError("Cannot change pins while the stream has a draft")
    stream = await lock_stream(session, stream_id)
    if not stream.has_offers:
        raise EditorConflictError("Stream does not contain offers")
    rows = await stream_rows(session, stream_id)
    try:
        changed = apply_action(_domain(rows), OfferAction("pin" if pinned else "unpin", offer_id))
    except WeightRuleError as exc:
        raise _rule_error(exc) from exc
    by_id = {row.offer_id: row for row in rows}
    for offer in changed:
        by_id[offer.offer_id].pinned = offer.pinned
    await session.commit()
    return await get_stream_snapshot(session, stream_id)
