"""PostgreSQL constraints for the published ORM schema."""

from collections.abc import AsyncIterator
from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import Base, create_engine
from app.models import Campaign, Stream, StreamOffer
from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
async def db_connection() -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = create_engine(TEST_DATABASE_URL)
    connection = await engine.connect()
    transaction = await connection.begin()
    try:
        await connection.run_sync(Base.metadata.create_all)
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


async def _seed_parent_rows(connection: AsyncConnection) -> tuple[int, int]:
    campaign_id = await connection.scalar(
        cast(Table, Campaign.__table__)
        .insert()
        .values(keitaro_id=101, name="Campaign", alias="alias", geo="AU")
        .returning(Campaign.id)
    )
    assert campaign_id is not None
    stream_id = await connection.scalar(
        cast(Table, Stream.__table__)
        .insert()
        .values(
            campaign_id=campaign_id,
            keitaro_id=201,
            name="Stream",
            position=0,
            has_offers=True,
        )
        .returning(Stream.id)
    )
    assert stream_id is not None
    return campaign_id, stream_id


async def test_defaults_and_archived_offer_are_persisted(
    db_connection: AsyncConnection,
) -> None:
    _, stream_id = await _seed_parent_rows(db_connection)
    await db_connection.execute(
        cast(Table, StreamOffer.__table__)
        .insert()
        .values(
            stream_id=stream_id,
            offer_id=301,
            offer_name="Archived offer",
            status="removed",
            weight=0,
            position=0,
        )
    )

    campaign = (await db_connection.execute(select(Campaign.__table__))).mappings().one()
    stream = (await db_connection.execute(select(Stream.__table__))).mappings().one()
    offer = (await db_connection.execute(select(StreamOffer.__table__))).mappings().one()
    assert campaign["status"] == "active"
    assert campaign["created_at"] is not None
    assert stream["status"] == "published"
    assert offer["status"] == "removed"
    assert offer["weight"] == 0
    assert offer["pinned"] is False


async def test_unique_ids_and_foreign_keys_are_enforced(
    db_connection: AsyncConnection,
) -> None:
    _, stream_id = await _seed_parent_rows(db_connection)
    valid = {
        "stream_id": stream_id,
        "offer_id": 301,
        "offer_name": "Offer",
        "status": "active",
        "weight": 100,
        "position": 0,
    }
    await db_connection.execute(cast(Table, StreamOffer.__table__).insert().values(**valid))

    with pytest.raises(IntegrityError):
        async with db_connection.begin_nested():
            await db_connection.execute(cast(Table, StreamOffer.__table__).insert().values(**valid))
    with pytest.raises(IntegrityError):
        async with db_connection.begin_nested():
            await db_connection.execute(
                cast(Table, StreamOffer.__table__)
                .insert()
                .values(**{**valid, "offer_id": 302, "stream_id": 99999})
            )
    with pytest.raises(IntegrityError):
        async with db_connection.begin_nested():
            await db_connection.execute(
                cast(Table, Campaign.__table__)
                .insert()
                .values(keitaro_id=101, name="Duplicate", alias="duplicate", geo="US")
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "unknown"},
        {"weight": 101},
        {"weight": -1},
        {"status": "removed", "weight": 1},
        {"status": "removed", "weight": 0, "pinned": True},
    ],
)
async def test_invalid_offer_state_is_rejected(
    db_connection: AsyncConnection, changes: dict[str, object]
) -> None:
    _, stream_id = await _seed_parent_rows(db_connection)
    row: dict[str, object] = {
        "stream_id": stream_id,
        "offer_id": 301,
        "offer_name": "Offer",
        "status": "active",
        "weight": 20,
        "pinned": False,
        "position": 0,
    }
    row.update(changes)

    with pytest.raises(IntegrityError):
        async with db_connection.begin_nested():
            await db_connection.execute(cast(Table, StreamOffer.__table__).insert().values(**row))
