"""Static geographic reference endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.domain.geo import COUNTRIES

router = APIRouter(prefix="/api", tags=["geo"])


class GeoOut(BaseModel):
    code: str
    name: str


@router.get("/geo")
async def list_geo() -> list[GeoOut]:
    return [GeoOut(code=code, name=name) for code, name in COUNTRIES]
