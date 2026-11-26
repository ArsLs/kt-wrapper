"""Orchestrate campaign creation with local transaction and upstream compensation."""

import asyncio
import json
import logging
import secrets
import string
from collections.abc import Callable
from typing import TypedDict
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.geo import is_valid_geo
from app.errors import AppError
from app.keitaro.client import KeitaroClient
from app.keitaro.errors import KeitaroError, KeitaroNotFoundError, KeitaroUpstreamError
from app.keitaro.schemas import (
    CampaignCreateIn,
    CreatedResourceOut,
    StreamCreateIn,
    StreamFilterIn,
    StreamOfferIn,
    StreamOut,
)
from app.logging_config import log_event
from app.models import Campaign, MutationOperation, Stream, StreamOffer
from app.services import idempotency
from app.services.offer_catalog import OfferCatalog

logger = logging.getLogger(__name__)
ALIAS_CHARS = string.ascii_letters + string.digits
CAMPAIGN_NAME_PREFIX = "test_laskarev_"
GEO_STREAM_NAME = "Geo redirect"
OFFERS_STREAM_NAME = "Offers"


class CreateError(AppError):
    status_code = 422
    code = "campaign_create_invalid"


class CleanupFailedError(AppError):
    status_code = 502
    code = "create_cleanup_failed"


class CreateUncertainError(AppError):
    status_code = 502
    code = "campaign_create_uncertain"

    def __init__(self, alias: str | None = None) -> None:
        detail = (
            "Campaign creation could not be confirmed. Retry this same Create to check its status "
            "before starting another one."
        )
        if alias is not None:
            detail += f" In Keitaro, check for campaign alias {alias}."
        super().__init__(detail)


class CampaignCreated(TypedDict):
    id: int
    keitaro_id: int
    name: str
    alias: str
    geo: str


def _response(campaign: Campaign) -> CampaignCreated:
    return {
        "id": campaign.id,
        "keitaro_id": campaign.keitaro_id,
        "name": campaign.name,
        "alias": campaign.alias,
        "geo": campaign.geo,
    }


async def create_idempotent(
    keitaro: KeitaroClient,
    session: AsyncSession,
    catalog: OfferCatalog,
    key: UUID,
    *,
    name: str,
    geo: str,
    offer_id: int,
) -> tuple[dict[str, object], bool]:
    request = {"name": name, "geo": geo, "offer_id": offer_id}
    try:
        async with idempotency.locked_create_session(session, key) as locked:
            await idempotency.reserve(locked, key, "create", request)
            operation = await idempotency.load(locked, key, "create", request)
            if operation.result is not None:
                return operation.result, True
            try:
                campaign, _ = await create_campaign(
                    keitaro,
                    locked,
                    catalog,
                    name=name,
                    geo=geo,
                    offer_id=offer_id,
                    operation=operation,
                )
            except CreateUncertainError:
                alias = inspect(operation).dict.get("alias")
                raise CreateUncertainError(alias) from None
            return dict(_response(campaign)), False
    except SQLAlchemyError as exc:
        log_event(
            logger,
            logging.ERROR,
            "campaign.create",
            stage="database",
            outcome="uncertain",
            exc_info=exc,
        )
        raise CreateUncertainError() from None


async def _recover_campaign(
    keitaro: KeitaroClient, session: AsyncSession, operation: MutationOperation, name: str
) -> CreatedResourceOut:
    try:
        matches = [item for item in await keitaro.list_campaigns() if item.alias == operation.alias]
    except KeitaroError:
        raise CreateUncertainError() from None
    if len(matches) != 1 or matches[0].name != name:
        raise CreateUncertainError()
    campaign_id = matches[0].id
    if operation.keitaro_campaign_id not in (None, campaign_id):
        raise CreateUncertainError()
    if await session.scalar(select(Campaign.id).where(Campaign.keitaro_id == campaign_id)):
        raise CreateUncertainError()
    operation.keitaro_campaign_id = campaign_id
    await session.commit()
    return CreatedResourceOut(id=campaign_id)


async def _start_campaign(
    keitaro: KeitaroClient,
    session: AsyncSession,
    payload: CampaignCreateIn,
    new_alias: Callable[[], str],
    operation: MutationOperation | None,
) -> tuple[CreatedResourceOut, str]:
    for attempt in range(3):
        alias = new_alias()
        if operation is not None:
            try:
                async with session.begin_nested():
                    operation.alias = alias
                    await session.flush()
            except IntegrityError:
                if attempt == 2:
                    raise CreateError("A campaign alias could not be reserved") from None
                await session.refresh(operation)
                continue
            await session.commit()
        try:
            created = await keitaro.create_campaign(payload.model_copy(update={"alias": alias}))
        except KeitaroUpstreamError as exc:
            if operation is not None:
                if exc.upstream_status not in (400, 401, 403, 409, 422):
                    raise CreateUncertainError() from None
                operation.alias = None
                await session.commit()
            if exc.upstream_status != 409 or attempt == 2:
                raise
        except KeitaroNotFoundError:
            if operation is not None:
                operation.alias = None
                await session.commit()
            raise
        except KeitaroError:
            if operation is not None:
                raise CreateUncertainError() from None
            raise
        else:
            if operation is not None:
                operation.keitaro_campaign_id = created.id
                await session.commit()
            return created, alias
    raise AssertionError("Campaign creation loop finished without a result")


def _matches_stream(stream: StreamOut, payload: StreamCreateIn) -> bool:
    return (
        stream.campaign_id == payload.campaign_id
        and stream.name == payload.name
        and stream.position == payload.position
        and stream.schema_ == payload.schema_
        and [(offer.offer_id, offer.share, offer.state) for offer in stream.offers]
        == [(offer.offer_id, offer.share, offer.state) for offer in payload.offers or []]
    )


def _filter_values(payload: str | list[str]) -> object:
    if isinstance(payload, list):
        return payload
    try:
        decoded: object = json.loads(payload)
    except json.JSONDecodeError:
        return [payload]
    return [decoded] if isinstance(decoded, str) else decoded


async def _recover_stream(
    keitaro: KeitaroClient, payload: StreamCreateIn, recorded_id: int | None
) -> int:
    try:
        streams = await keitaro.list_campaign_streams(payload.campaign_id)
        matches = [
            stream.id
            for stream in streams
            if (recorded_id is None or stream.id == recorded_id)
            and _matches_stream(stream, payload)
        ]
        if len(matches) != 1:
            raise CreateUncertainError()
        stream = await keitaro.get_stream_for_recovery(matches[0])
    except KeitaroError:
        raise CreateUncertainError() from None
    if (
        stream.id != matches[0]
        or not _matches_stream(stream, payload)
        or stream.type != payload.type
        or stream.state != "active"
        or stream.landings
        or [(item.name, item.mode, _filter_values(item.payload)) for item in stream.filters]
        != [(item.name, item.mode, item.payload) for item in payload.filters or []]
    ):
        raise CreateUncertainError()
    if payload.schema_ == "redirect" and (
        stream.action_type != payload.action_type or stream.action_payload != payload.action_payload
    ):
        raise CreateUncertainError()
    return stream.id


async def _ensure_stream(
    keitaro: KeitaroClient,
    session: AsyncSession,
    payload: StreamCreateIn,
    operation: MutationOperation | None,
    recovering: bool,
) -> int:
    if operation is None:
        return (await keitaro.create_stream(payload)).id

    position = payload.position
    recorded_id = operation.geo_stream_id if position == 0 else operation.offers_stream_id
    if recorded_id is not None or operation.pending_stream == position:
        stream_id = (
            await _recover_stream(keitaro, payload, recorded_id) if recovering else recorded_id
        )
        if stream_id is None:
            raise CreateUncertainError()
    else:
        operation.pending_stream = position
        await session.commit()
        stream_id = (await keitaro.create_stream(payload)).id

    if position == 0:
        operation.geo_stream_id = stream_id
    else:
        operation.offers_stream_id = stream_id
    if operation.pending_stream == position:
        operation.pending_stream = None
    await session.commit()
    return stream_id


async def _cleanup_campaign(
    keitaro: KeitaroClient, session: AsyncSession, operation: MutationOperation
) -> None:
    campaign_id = operation.keitaro_campaign_id
    if campaign_id is None:
        raise CreateUncertainError()
    try:
        await keitaro.delete_campaign(campaign_id)
    except KeitaroNotFoundError:
        pass
    except KeitaroError as exc:
        log_event(
            logger,
            logging.ERROR,
            "campaign.create",
            stage="cleanup",
            outcome="uncertain",
            keitaro_campaign_id=campaign_id,
            exc_info=exc,
        )
        raise CreateUncertainError() from None
    operation.alias = None
    operation.keitaro_campaign_id = None
    operation.pending_stream = None
    operation.geo_stream_id = None
    operation.offers_stream_id = None
    operation.offer_name = None
    operation.cleanup_pending = False
    await session.commit()
    log_event(
        logger,
        logging.INFO,
        "campaign.create",
        stage="cleanup",
        outcome="succeeded",
        keitaro_campaign_id=campaign_id,
    )


async def create_campaign(
    keitaro: KeitaroClient,
    session: AsyncSession,
    catalog: OfferCatalog,
    *,
    name: str,
    geo: str,
    offer_id: int,
    alias_factory: Callable[[], str] | None = None,
    operation: MutationOperation | None = None,
) -> tuple[Campaign, int]:
    clean_name = name.strip()
    if not clean_name:
        raise CreateError("Campaign name must not be empty")
    if not clean_name.startswith(CAMPAIGN_NAME_PREFIX):
        clean_name = f"{CAMPAIGN_NAME_PREFIX}{clean_name}"
    if not is_valid_geo(geo):
        raise CreateError("Geo must be a valid uppercase ISO-2 code")
    if offer_id < 1:
        raise CreateError("Offer ID must be a positive integer")

    if operation is not None and operation.cleanup_pending:
        await _cleanup_campaign(keitaro, session, operation)
    campaign_alias = operation.alias if operation is not None else None
    recovering = campaign_alias is not None
    if campaign_alias is not None and operation is not None:
        if operation.offer_name is None:
            raise CreateUncertainError()
        offer_name = operation.offer_name
        created = await _recover_campaign(keitaro, session, operation, clean_name)
    else:
        offer = await catalog.get_active(keitaro, offer_id)
        if offer is None:
            raise CreateError("Selected offer is not active or does not exist")
        offer_name = offer.name
        domains, groups, sources = await asyncio.gather(
            keitaro.list_domains(), keitaro.list_groups("campaigns"), keitaro.list_traffic_sources()
        )
        catalogs = (("domain", domains), ("campaign group", groups), ("traffic source", sources))
        for label, values in catalogs:
            if len(values) != 1:
                raise CreateError(f"Expected exactly one {label}; found {len(values)}")

        new_alias = alias_factory or (
            lambda: "".join(secrets.choice(ALIAS_CHARS) for _ in range(6))
        )
        if operation is not None:
            operation.offer_name = offer_name
        created, campaign_alias = await _start_campaign(
            keitaro,
            session,
            CampaignCreateIn(
                name=clean_name,
                alias="",
                type="position",
                domain_id=domains[0].id,
                group_id=groups[0].id,
                traffic_source_id=sources[0].id,
                cost_type="CPC",
                cost_currency="USD",
                cost_auto=True,
            ),
            new_alias,
            operation,
        )

    log_event(
        logger,
        logging.INFO,
        "campaign.create",
        stage="keitaro_campaign",
        outcome="recovered" if recovering else "created",
        keitaro_campaign_id=created.id,
    )
    stage = "geo_stream"
    try:
        geo_stream_id = await _ensure_stream(
            keitaro,
            session,
            StreamCreateIn(
                campaign_id=created.id,
                name=GEO_STREAM_NAME,
                position=0,
                schema="redirect",
                type="regular",
                action_type="http",
                action_payload="https://google.com",
                filters=[StreamFilterIn(name="country", mode="accept", payload=[geo])],
                offers=[],
                landings=[],
            ),
            operation,
            recovering,
        )
        stage = "offers_stream"
        offers_stream_id = await _ensure_stream(
            keitaro,
            session,
            StreamCreateIn(
                campaign_id=created.id,
                name=OFFERS_STREAM_NAME,
                position=1,
                schema="landings",
                type="regular",
                action_type="http",
                filters=[],
                offers=[StreamOfferIn(offer_id=offer_id, share=100, state="active")],
                landings=[],
            ),
            operation,
            recovering,
        )
        stage = "persist"
        campaign = Campaign(keitaro_id=created.id, name=clean_name, alias=campaign_alias, geo=geo)
        session.add(campaign)
        await session.flush()
        campaign_id = campaign.id
        geo_row = Stream(
            campaign_id=campaign_id,
            keitaro_id=geo_stream_id,
            name=GEO_STREAM_NAME,
            position=0,
            has_offers=False,
            status="published",
        )
        offers_row = Stream(
            campaign_id=campaign_id,
            keitaro_id=offers_stream_id,
            name=OFFERS_STREAM_NAME,
            position=1,
            has_offers=True,
            status="published",
        )
        session.add_all([geo_row, offers_row])
        await session.flush()
        session.add(
            StreamOffer(
                stream_id=offers_row.id,
                offer_id=offer_id,
                offer_name=offer_name,
                status="active",
                weight=100,
                pinned=False,
                position=0,
            )
        )
        if operation is not None:
            operation.result = dict(_response(campaign))
            await session.flush()
        await session.commit()
    except BaseException as exc:
        log_event(
            logger,
            logging.ERROR,
            "campaign.create",
            stage=stage,
            outcome="uncertain" if isinstance(exc, CreateUncertainError) else "failed",
            keitaro_campaign_id=created.id,
            exc_info=exc,
        )
        if isinstance(exc, CreateUncertainError):
            raise
        if operation is not None and isinstance(exc, (SQLAlchemyError, asyncio.CancelledError)):
            # A disconnect can release the advisory lock. Let the next owner reconcile the
            # persisted checkpoints before performing any further upstream writes.
            raise
        rollback_error: BaseException | None = None
        try:
            await session.rollback()
        except BaseException as rollback_exc:
            rollback_error = rollback_exc
            log_event(
                logger,
                logging.ERROR,
                "campaign.create",
                stage="rollback",
                outcome="failed",
                keitaro_campaign_id=created.id,
                exc_info=rollback_exc,
            )
        if operation is not None:
            if rollback_error is not None:
                raise CreateUncertainError() from None
            await session.refresh(operation)
            operation.cleanup_pending = True
            await session.commit()
            await _cleanup_campaign(keitaro, session, operation)
        else:
            try:
                await asyncio.shield(keitaro.delete_campaign(created.id))
            except KeitaroNotFoundError:
                pass
            except BaseException as cleanup_exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "campaign.create",
                    stage="cleanup",
                    outcome="failed",
                    keitaro_campaign_id=created.id,
                    exc_info=cleanup_exc,
                )
                raise CleanupFailedError(
                    f"Campaign {created.id} could not be removed after a failed create"
                ) from None
            log_event(
                logger,
                logging.INFO,
                "campaign.create",
                stage="cleanup",
                outcome="succeeded",
                keitaro_campaign_id=created.id,
            )
        if rollback_error is not None or isinstance(exc, SQLAlchemyError):
            raise AppError("Campaign local state could not be saved") from None
        raise
    log_event(
        logger,
        logging.INFO,
        "campaign.create",
        stage="persist",
        outcome="succeeded",
        campaign_id=campaign_id,
        keitaro_campaign_id=created.id,
        keitaro_geo_stream_id=geo_stream_id,
        keitaro_offers_stream_id=offers_stream_id,
    )
    return campaign, created.id
