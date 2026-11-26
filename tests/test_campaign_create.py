from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db import Base, get_session
from app.errors import AppError
from app.keitaro.client import KeitaroClient
from app.keitaro.errors import KeitaroUpstreamError
from app.keitaro.schemas import (
    CampaignCreateIn,
    CreatedResourceOut,
    DomainOut,
    GroupOut,
    OfferOut,
    StreamCreateIn,
    TrafficSourceOut,
)
from app.models import Campaign, Stream, StreamOffer
from app.services.campaign_create import (
    CleanupFailedError,
    CreateError,
    create_campaign,
)
from app.services.offer_catalog import OfferCatalog
from tests.conftest import TEST_DATABASE_URL


class FakeKeitaro:
    def __init__(
        self,
        fail_stream: int | None = None,
        domain_count: int = 1,
        conflicts: int = 0,
        fail_cleanup: bool = False,
        campaign_error: int | None = None,
        offer_state: str = "active",
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail_stream = fail_stream
        self.stream_calls = 0
        self.domain_count = domain_count
        self.conflicts = conflicts
        self.fail_cleanup = fail_cleanup
        self.campaign_error = campaign_error
        self.offer_state = offer_state

    async def list_offers(self) -> list[OfferOut]:
        self.calls.append(("list_offers", None))
        return [OfferOut(id=42, name="Offer Forty Two", state=self.offer_state)]

    async def list_domains(self) -> list[DomainOut]:
        self.calls.append(("list_domains", None))
        return [DomainOut(id=index + 1, name="domain") for index in range(self.domain_count)]

    async def list_groups(self, group_type: str) -> list[GroupOut]:
        self.calls.append(("list_groups", group_type))
        return [GroupOut(id=2, name="group")]

    async def list_traffic_sources(self) -> list[TrafficSourceOut]:
        self.calls.append(("list_traffic_sources", None))
        return [TrafficSourceOut(id=3, name="source")]

    async def create_campaign(self, payload: CampaignCreateIn) -> CreatedResourceOut:
        self.calls.append(("create_campaign", payload))
        if self.conflicts > 0:
            self.conflicts -= 1
            raise KeitaroUpstreamError(409)
        if self.campaign_error is not None:
            raise KeitaroUpstreamError(self.campaign_error)
        return CreatedResourceOut(id=100)

    async def create_stream(self, payload: StreamCreateIn) -> CreatedResourceOut:
        self.stream_calls += 1
        self.calls.append(("create_stream", payload))
        if self.fail_stream == self.stream_calls:
            raise RuntimeError("synthetic upstream failure")
        return CreatedResourceOut(id=200 + self.stream_calls)

    async def delete_campaign(self, campaign_id: int) -> None:
        self.calls.append(("delete_campaign", campaign_id))
        if self.fail_cleanup:
            raise RuntimeError("synthetic cleanup failure")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(Base.metadata.create_all)
        session = AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def test_create_campaign_builds_ordered_streams_and_persists_state(
    db_session: AsyncSession,
) -> None:
    keitaro = FakeKeitaro()
    catalog = OfferCatalog()
    campaign, external_id = await create_campaign(
        cast(KeitaroClient, keitaro),
        db_session,
        catalog,
        name="  Example  ",
        geo="AU",
        offer_id=42,
        alias_factory=lambda: "Ab123Z",
    )
    assert external_id == 100
    assert campaign.name == "test_laskarev_Example"
    assert campaign.alias == "Ab123Z"
    campaign_payload = next(value for call, value in keitaro.calls if call == "create_campaign")
    assert campaign_payload.name == "test_laskarev_Example"
    assert campaign_payload.cost_auto is True
    payloads = [value for call, value in keitaro.calls if call == "create_stream"]
    assert [item.position for item in payloads] == [0, 1]
    assert payloads[0].filters[0].payload == ["AU"]
    assert payloads[0].action_payload == "https://google.com"
    assert payloads[1].offers[0].share == 100
    rows = list(await db_session.scalars(select(Stream).order_by(Stream.position)))
    offer_rows = list(await db_session.scalars(select(StreamOffer)))
    assert [row.keitaro_id for row in rows] == [201, 202]
    assert len(offer_rows) == 1
    assert (offer_rows[0].status, offer_rows[0].weight, offer_rows[0].pinned) == (
        "active",
        100,
        False,
    )
    assert not any(call == "delete_campaign" for call, _ in keitaro.calls)


async def test_stream_failure_compensates_and_keeps_database_empty(
    db_session: AsyncSession,
) -> None:
    keitaro = FakeKeitaro(fail_stream=2)
    with pytest.raises(RuntimeError):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="AU",
            offer_id=42,
            alias_factory=lambda: "Ab123Z",
        )
    assert ("delete_campaign", 100) in keitaro.calls
    assert list(await db_session.scalars(select(Campaign))) == []
    assert list(await db_session.scalars(select(Stream))) == []


async def test_invalid_geo_fails_before_any_upstream_call(db_session: AsyncSession) -> None:
    keitaro = FakeKeitaro()
    with pytest.raises(CreateError):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="ZZ",
            offer_id=42,
        )
    assert keitaro.calls == []


async def test_create_request_rejects_boolean_offer_id(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/campaigns", json={"name": "Example", "geo": "AU", "offer_id": True}
    )
    assert response.status_code == 422


async def test_alias_conflict_retries_with_a_new_value(db_session: AsyncSession) -> None:
    keitaro = FakeKeitaro(conflicts=1)
    aliases = iter(("First1", "Second2"))
    await create_campaign(
        cast(KeitaroClient, keitaro),
        db_session,
        OfferCatalog(),
        name="Example",
        geo="AU",
        offer_id=42,
        alias_factory=lambda: next(aliases),
    )
    campaign_payloads = [value for call, value in keitaro.calls if call == "create_campaign"]
    assert [payload.alias for payload in campaign_payloads] == ["First1", "Second2"]
    assert len(campaign_payloads) == 2


async def test_non_conflict_campaign_error_is_not_retried(db_session: AsyncSession) -> None:
    keitaro = FakeKeitaro(campaign_error=502)
    with pytest.raises(KeitaroUpstreamError):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="AU",
            offer_id=42,
        )
    assert len([call for call, _ in keitaro.calls if call == "create_campaign"]) == 1


@pytest.mark.parametrize("domain_count", [0, 2])
async def test_wrong_catalog_count_fails_before_campaign_post(
    db_session: AsyncSession, domain_count: int
) -> None:
    keitaro = FakeKeitaro(domain_count=domain_count)
    with pytest.raises(CreateError, match=f"exactly one domain; found {domain_count}"):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="AU",
            offer_id=42,
        )
    assert not any(call == "create_campaign" for call, _ in keitaro.calls)


async def test_commit_failure_compensates_even_if_rollback_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    keitaro = FakeKeitaro()
    monkeypatch.setattr(db_session, "commit", AsyncMock(side_effect=SQLAlchemyError("commit")))
    monkeypatch.setattr(db_session, "rollback", AsyncMock(side_effect=RuntimeError("rollback")))
    with pytest.raises(AppError, match="local state could not be saved"):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="AU",
            offer_id=42,
        )
    assert ("delete_campaign", 100) in keitaro.calls


async def test_cleanup_failure_reports_manual_cleanup_id(db_session: AsyncSession) -> None:
    keitaro = FakeKeitaro(fail_stream=1, fail_cleanup=True)
    with pytest.raises(CleanupFailedError, match="100"):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="AU",
            offer_id=42,
        )


async def test_create_api_returns_local_and_external_ids(
    client: httpx.AsyncClient, app: FastAPI, db_session: AsyncSession, respx_mock: respx.MockRouter
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        base = "https://keitaro.test/admin_api/v1"
        respx_mock.get(f"{base}/offers").respond(
            200, json=[{"id": 42, "name": "Offer Forty Two", "state": "active"}]
        )
        respx_mock.get(f"{base}/domains").respond(200, json=[{"id": 1, "name": "domain"}])
        respx_mock.get(f"{base}/groups", params={"type": "campaigns"}).respond(
            200, json=[{"id": 2, "name": "group"}]
        )
        respx_mock.get(f"{base}/traffic_sources").respond(200, json=[{"id": 3, "name": "source"}])
        respx_mock.post(f"{base}/campaigns").respond(201, json={"id": 100})
        respx_mock.post(f"{base}/streams").mock(
            side_effect=[
                httpx.Response(201, json={"id": 201}),
                httpx.Response(201, json={"id": 202}),
            ]
        )
        response = await client.post(
            "/api/campaigns", json={"name": "test_laskarev_Example", "geo": "AU", "offer_id": 42}
        )
        assert response.status_code == 201
        result = response.json()
        assert result["id"] > 0
        assert result["keitaro_id"] == 100
        assert result["name"] == "test_laskarev_Example"
        assert len(result["alias"]) == 6
        assert result["geo"] == "AU"
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_inactive_offer_fails_before_resource_creation(db_session: AsyncSession) -> None:
    keitaro = FakeKeitaro(offer_state="paused")
    with pytest.raises(CreateError, match="not active"):
        await create_campaign(
            cast(KeitaroClient, keitaro),
            db_session,
            OfferCatalog(),
            name="Example",
            geo="AU",
            offer_id=42,
        )
    assert [call for call, _ in keitaro.calls] == ["list_offers"]
