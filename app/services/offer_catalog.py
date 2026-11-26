"""Application-scoped, short-lived Keitaro offer catalog cache."""

import asyncio
import builtins
import time

from app.keitaro.client import KeitaroClient
from app.keitaro.schemas import OfferOut


class OfferCatalog:
    """Cache offers for ten seconds; list() is also suitable for Fetch name lookup."""

    def __init__(self, ttl: float = 10.0) -> None:
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._offers: list[OfferOut] | None = None
        self._expires_at = 0.0

    async def list(self, keitaro: KeitaroClient) -> list[OfferOut]:
        now = time.monotonic()
        if self._offers is not None and now < self._expires_at:
            return list(self._offers)
        async with self._lock:
            now = time.monotonic()
            if self._offers is not None and now < self._expires_at:
                return list(self._offers)
            offers = await keitaro.list_offers()
            self._offers = offers
            self._expires_at = time.monotonic() + self._ttl
            return list(offers)

    def cached(self) -> builtins.list[OfferOut] | None:
        """Return a fresh cached batch without performing a Keitaro request."""
        if self._offers is None or time.monotonic() >= self._expires_at:
            return None
        return list(self._offers)

    async def get_active(self, keitaro: KeitaroClient, offer_id: int) -> OfferOut | None:
        return next(
            (
                offer
                for offer in await self.list(keitaro)
                if offer.id == offer_id and offer.state == "active"
            ),
            None,
        )
