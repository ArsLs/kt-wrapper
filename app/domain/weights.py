"""Offer weight distribution and state transition rules."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

OfferStatus = Literal["active", "added", "removed"]
ActionKind = Literal["add", "remove", "bring_back", "pin", "unpin"]
_PARTICIPANT_STATUSES = ("active", "added")


class WeightRuleError(ValueError):
    """Raised when an offer snapshot or requested transition violates domain rules."""


@dataclass(frozen=True)
class OfferWeight:
    offer_id: int
    position: int
    status: OfferStatus
    weight: int
    pinned: bool


@dataclass(frozen=True)
class OfferAction:
    kind: ActionKind
    offer_id: int


def _ordered(offers: Sequence[OfferWeight]) -> list[OfferWeight]:
    return sorted(offers, key=lambda offer: (offer.position, offer.offer_id))


def _validate_snapshot(offers: Sequence[OfferWeight]) -> None:
    identifiers: set[int] = set()
    for offer in offers:
        if isinstance(offer.offer_id, bool) or not isinstance(offer.offer_id, int):
            raise WeightRuleError("offer_id must be an integer")
        if offer.offer_id in identifiers:
            raise WeightRuleError("offer_id values must be unique")
        identifiers.add(offer.offer_id)
        if isinstance(offer.position, bool) or not isinstance(offer.position, int):
            raise WeightRuleError("position must be an integer")
        if offer.position < 0:
            raise WeightRuleError("position must be non-negative")
        if offer.status not in ("active", "added", "removed"):
            raise WeightRuleError("unknown offer status")
        if isinstance(offer.weight, bool) or not isinstance(offer.weight, int):
            raise WeightRuleError("weight must be an integer")
        if not 0 <= offer.weight <= 100:
            raise WeightRuleError("weight must be between 0 and 100")
        if not isinstance(offer.pinned, bool):
            raise WeightRuleError("pinned must be a boolean")
        if offer.status == "removed" and (offer.weight != 0 or offer.pinned):
            raise WeightRuleError("removed offers must have zero weight and no pin")
        if offer.status == "added" and offer.pinned:
            raise WeightRuleError("added offers cannot be pinned")

    participants = [offer for offer in offers if offer.status in _PARTICIPANT_STATUSES]
    if participants:
        pinned_total = sum(offer.weight for offer in participants if offer.pinned)
        if not any(not offer.pinned for offer in participants):
            raise WeightRuleError("at least one participant must be unpinned")
        if pinned_total >= 100:
            raise WeightRuleError("pinned weights must total less than 100")


def validate_distribution(offers: Sequence[OfferWeight]) -> None:
    """Validate structural rules and a complete 100-percent participant distribution."""
    _validate_snapshot(offers)
    participants = [offer for offer in offers if offer.status in _PARTICIPANT_STATUSES]
    if participants and sum(offer.weight for offer in participants) != 100:
        raise WeightRuleError("participant weights must total 100")


def recalculate(offers: Sequence[OfferWeight]) -> list[OfferWeight]:
    """Redistribute the unpinned remainder and return a stable, independent list."""
    _validate_snapshot(offers)
    result = _ordered(offers)
    participants = [offer for offer in result if offer.status in _PARTICIPANT_STATUSES]
    if not participants:
        return result

    unpinned = [offer for offer in participants if not offer.pinned]
    pinned_total = sum(offer.weight for offer in participants if offer.pinned)
    base, extra = divmod(100 - pinned_total, len(unpinned))
    extras_start = len(unpinned) - extra
    updates: dict[int, OfferWeight] = {}
    for index, offer in enumerate(unpinned):
        updates[offer.offer_id] = replace(offer, weight=base + int(index >= extras_start))
    result = [updates.get(offer.offer_id, offer) for offer in result]
    validate_distribution(result)
    return result


def _find(offers: Sequence[OfferWeight], offer_id: int) -> OfferWeight | None:
    return next((offer for offer in offers if offer.offer_id == offer_id), None)


def can_pin(offers: Sequence[OfferWeight], offer_id: int) -> bool:
    """Return whether an active unpinned offer can be pinned safely."""
    _validate_snapshot(offers)
    if isinstance(offer_id, bool) or not isinstance(offer_id, int):
        return False
    target = _find(offers, offer_id)
    if target is None or target.status != "active" or target.pinned:
        return False
    participants = [offer for offer in offers if offer.status in _PARTICIPANT_STATUSES]
    unpinned_count = sum(not offer.pinned for offer in participants)
    pinned_total = sum(offer.weight for offer in participants if offer.pinned)
    return unpinned_count > 1 and pinned_total + target.weight < 100


def can_remove(offers: Sequence[OfferWeight], offer_id: int) -> bool:
    """Return whether removing an active offer preserves the unpinned escape offer."""
    validate_distribution(offers)
    if isinstance(offer_id, bool) or not isinstance(offer_id, int):
        return False
    target = _find(offers, offer_id)
    if target is None or target.status not in _PARTICIPANT_STATUSES:
        return False
    participants = [offer for offer in offers if offer.status in _PARTICIPANT_STATUSES]
    other_unpinned = sum(not offer.pinned and offer.offer_id != offer_id for offer in participants)
    other_pinned = any(offer.pinned and offer.offer_id != offer_id for offer in participants)
    return other_unpinned > 0 or not other_pinned


def apply_action(offers: Sequence[OfferWeight], action: OfferAction) -> list[OfferWeight]:
    """Apply an offer transition or change a pin at the current weight."""
    if action.kind in ("pin", "unpin"):
        _validate_snapshot(offers)
    else:
        validate_distribution(offers)
    if action.kind not in ("add", "remove", "bring_back", "pin", "unpin"):
        raise WeightRuleError("unknown action")
    if isinstance(action.offer_id, bool) or not isinstance(action.offer_id, int):
        raise WeightRuleError("offer_id must be an integer")

    result = list(offers)
    target = _find(offers, action.offer_id)
    if action.kind == "add":
        if target is not None:
            if target.status == "removed":
                return apply_action(offers, OfferAction("bring_back", action.offer_id))
            raise WeightRuleError("offer is already present")
        position = max((offer.position for offer in offers), default=-1) + 1
        result.append(OfferWeight(action.offer_id, position, "added", 0, False))
        return recalculate(result)

    if action.kind == "bring_back":
        if target is None or target.status != "removed":
            raise WeightRuleError("only removed offers can be brought back")
        result = [
            replace(offer, status="active", pinned=False)
            if offer.offer_id == action.offer_id
            else offer
            for offer in result
        ]
        return recalculate(result)

    if action.kind == "remove":
        if target is None or target.status not in _PARTICIPANT_STATUSES:
            raise WeightRuleError("only active offers can be removed")
        if not can_remove(offers, action.offer_id):
            raise WeightRuleError(
                "removing this offer would leave pinned offers without an unpinned offer"
            )
        if target.status == "added":
            result = [offer for offer in result if offer.offer_id != action.offer_id]
        else:
            result = [
                replace(offer, status="removed", weight=0, pinned=False)
                if offer.offer_id == action.offer_id
                else offer
                for offer in result
            ]
        return recalculate(result)

    if action.kind == "pin":
        if not can_pin(offers, action.offer_id):
            raise WeightRuleError("offer cannot be pinned")
        result = [
            replace(offer, pinned=True) if offer.offer_id == action.offer_id else offer
            for offer in result
        ]
        return _ordered(result)

    if target is None or target.status != "active" or not target.pinned:
        raise WeightRuleError("only pinned active offers can be unpinned")
    result = [
        replace(offer, pinned=False) if offer.offer_id == action.offer_id else offer
        for offer in result
    ]
    return _ordered(result)
