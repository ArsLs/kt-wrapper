"""Request and response models for the subset of Keitaro used by the wrapper."""

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


class KeitaroModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CampaignOut(KeitaroModel):
    id: int
    name: str
    alias: str | None = None
    state: str | None = None
    type: str | None = None


INVALID_OFFER_WEIGHT = "keitaro_invalid_offer_weight"


class StreamOfferOut(KeitaroModel):
    offer_id: int
    share: int
    state: str
    name: str | None = None

    @field_validator("share", mode="wrap")
    @classmethod
    def share_within_range(cls, value: object, validate_int: Callable[[object], int]) -> int:
        cls._check_share_range(value)
        share = validate_int(value)
        cls._check_share_range(share)
        return share

    @staticmethod
    def _check_share_range(value: object) -> None:
        if isinstance(value, str):
            try:
                value = Decimal(value)
            except InvalidOperation:
                return
        if isinstance(value, Decimal) and value.is_nan():
            return
        if isinstance(value, (int, float, Decimal)) and (value < 0 or value > 100):
            raise PydanticCustomError(
                INVALID_OFFER_WEIGHT, "Offer weight must be between 0 and 100"
            )


class StreamOut(KeitaroModel):
    id: int
    name: str
    campaign_id: int
    position: int
    schema_: str | None = Field(default=None, alias="schema")
    state: str | None = None
    offers: list[StreamOfferOut] = Field(default_factory=list)


class StreamFilterRecoveryOut(KeitaroModel):
    name: str
    mode: str
    payload: str | list[str]


class StreamRecoveryOut(StreamOut):
    type: str
    state: str
    action_type: str | None = None
    action_payload: str | dict[str, object] | None = None
    filters: list[StreamFilterRecoveryOut]
    landings: list[dict[str, object]]
    offers: list[StreamOfferOut]


class OfferOut(KeitaroModel):
    id: int
    name: str
    state: str


class DomainOut(KeitaroModel):
    id: int
    name: str


class GroupOut(KeitaroModel):
    id: int
    name: str


class TrafficSourceOut(KeitaroModel):
    id: int
    name: str


class CreatedResourceOut(KeitaroModel):
    id: int


class CampaignCreateIn(BaseModel):
    name: str
    alias: str
    type: str
    domain_id: int
    group_id: int
    traffic_source_id: int
    cost_type: str | None = None
    cost_currency: str | None = None
    cost_auto: bool | None = None


class CampaignUpdateIn(BaseModel):
    name: str | None = None
    alias: str | None = None
    type: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None
    cost_type: str | None = None
    cost_currency: str | None = None


class StreamFilterIn(BaseModel):
    name: str
    mode: Literal["accept", "reject"]
    payload: list[str]


class StreamOfferIn(BaseModel):
    offer_id: int
    share: int
    state: Literal["active", "disabled"]


class StreamCreateIn(BaseModel):
    campaign_id: int
    name: str
    position: int
    schema_: str = Field(alias="schema")
    type: str
    action_type: str
    action_payload: str | None = None
    filters: list[StreamFilterIn] | None = None
    offers: list[StreamOfferIn] | None = None
    landings: list[dict[str, object]] | None = None


class StreamUpdateIn(BaseModel):
    name: str | None = None
    position: int | None = None
    schema_: str | None = Field(default=None, alias="schema")
    state: str | None = None
    action_type: str | None = None
    action_payload: str | None = None
    filters: list[StreamFilterIn] | None = None
    offers: list[StreamOfferIn] | None = None
    landings: list[dict[str, object]] | None = None
