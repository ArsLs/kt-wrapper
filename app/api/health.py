"""Liveness/readiness endpoint. Never calls Keitaro."""

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionDep
from app.logging_config import log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


class HealthOut(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthOut)
async def health(session: SessionDep) -> HealthOut | JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        log_event(logger, logging.ERROR, "health.database_unavailable", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "database": "unavailable"},
        )
    return HealthOut(status="ok", database="ok")
