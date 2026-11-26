"""Reserve mutation keys in PostgreSQL and serialize operations on the same key."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Header
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.errors import AppError
from app.models import MutationOperation

IdempotencyKey = Annotated[UUID | None, Header(alias="Idempotency-Key")]
REPLAY_HEADER = "Idempotency-Replayed"


class IdempotencyConflictError(AppError):
    status_code = 409
    code = "idempotency_conflict"

    def __init__(self) -> None:
        super().__init__("This idempotency key was already used for a different request.")


class IdempotencyResultUnavailableError(AppError):
    status_code = 409
    code = "idempotency_result_unavailable"

    def __init__(self) -> None:
        super().__init__("No successful result is stored for this idempotency key.")


async def reserve(session: AsyncSession, key: UUID, kind: str, request: dict[str, object]) -> None:
    await session.execute(
        insert(MutationOperation)
        .values(key=key, kind=kind, request=request)
        .on_conflict_do_nothing(index_elements=[MutationOperation.key])
    )
    await session.commit()


async def load(
    session: AsyncSession, key: UUID, kind: str, request: dict[str, object]
) -> MutationOperation:
    operation = (
        await session.scalars(
            select(MutationOperation).where(MutationOperation.key == key).with_for_update()
        )
    ).one_or_none()
    if operation is None:
        raise IdempotencyResultUnavailableError()
    if operation.kind != kind or operation.request != request:
        raise IdempotencyConflictError()
    return operation


@asynccontextmanager
async def _create_lock(connection: AsyncConnection, key: UUID) -> AsyncIterator[None]:
    # A session advisory lock remains held while Create commits recovery checkpoints.
    lock_id = int.from_bytes(key.bytes[:8], signed=True)
    try:
        await connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
        await connection.commit()
        yield
    finally:
        try:
            await connection.rollback()
            if not connection.invalidated:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                )
                await connection.commit()
        except BaseException:
            # A pooled connection must never retain the advisory lock.
            await connection.invalidate()
            raise


@asynccontextmanager
async def locked_create_session(session: AsyncSession, key: UUID) -> AsyncIterator[AsyncSession]:
    bind = session.bind
    if isinstance(bind, AsyncConnection):
        # A caller may already supply a session on a dedicated connection.
        lock_id = int.from_bytes(key.bytes[:8], signed=True)
        try:
            await session.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
            yield session
        finally:
            try:
                await session.rollback()
                await session.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id}
                )
                await session.commit()
            except BaseException:
                await session.invalidate()
                raise
    elif isinstance(bind, AsyncEngine):
        async with (
            bind.connect() as connection,
            _create_lock(connection, key),
            AsyncSession(bind=connection, expire_on_commit=False) as locked_session,
        ):
            yield locked_session
    else:
        raise RuntimeError("Create requires a PostgreSQL session")
