"""Published campaign, stream and offer state stored by the wrapper."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

__all__ = ["Base", "Campaign", "MutationOperation", "Stream", "StreamOffer"]


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("keitaro_id", name="uq_campaigns_keitaro_id"),
        CheckConstraint("status IN ('active', 'removed')", name="ck_campaigns_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    geo: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Stream(Base):
    __tablename__ = "streams"
    __table_args__ = (
        UniqueConstraint("keitaro_id", name="uq_streams_keitaro_id"),
        CheckConstraint("position >= 0", name="ck_streams_position_nonnegative"),
        CheckConstraint("status IN ('published', 'removed')", name="ck_streams_status"),
        Index("ix_streams_campaign_id", "campaign_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", name="fk_streams_campaign_id_campaigns"), nullable=False
    )
    keitaro_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    has_offers: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'published'")
    )


class StreamOffer(Base):
    __tablename__ = "stream_offers"
    __table_args__ = (
        UniqueConstraint("stream_id", "offer_id", name="uq_stream_offers_stream_offer"),
        CheckConstraint("status IN ('active', 'removed')", name="ck_stream_offers_status"),
        CheckConstraint("weight BETWEEN 0 AND 100", name="ck_stream_offers_weight_range"),
        CheckConstraint("position >= 0", name="ck_stream_offers_position_nonnegative"),
        CheckConstraint(
            "status != 'removed' OR (weight = 0 AND NOT pinned)",
            name="ck_stream_offers_removed_weight_pin",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int] = mapped_column(
        ForeignKey("streams.id", name="fk_stream_offers_stream_id_streams"), nullable=False
    )
    offer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    offer_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class MutationOperation(Base):
    __tablename__ = "mutation_operations"
    __table_args__ = (UniqueConstraint("alias", name="uq_mutation_operations_alias"),)

    key: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    request: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    alias: Mapped[str | None] = mapped_column(Text)
    offer_name: Mapped[str | None] = mapped_column(Text)
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    keitaro_campaign_id: Mapped[int | None] = mapped_column(BigInteger)
    pending_stream: Mapped[int | None] = mapped_column(Integer)
    geo_stream_id: Mapped[int | None] = mapped_column(BigInteger)
    offers_stream_id: Mapped[int | None] = mapped_column(BigInteger)
