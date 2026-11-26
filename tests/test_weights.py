"""Tests for offer distribution and transitions."""

from collections.abc import Sequence
from typing import cast

import pytest

from app.domain.weights import (
    OfferAction,
    OfferWeight,
    WeightRuleError,
    apply_action,
    can_pin,
    can_remove,
    recalculate,
    validate_distribution,
)


def offers(*rows: tuple[int, int, str, int, bool]) -> list[OfferWeight]:
    return [OfferWeight(*row) for row in rows]  # type: ignore[arg-type]


def signature(items: Sequence[OfferWeight]) -> list[tuple[int, int, str, int, bool]]:
    return [(o.offer_id, o.position, o.status, o.weight, o.pinned) for o in items]


def test_recalculate_examples_and_stability() -> None:
    original = offers(
        (3, 2, "active", 34, False), (1, 0, "active", 33, False), (2, 1, "active", 33, False)
    )
    assert [o.weight for o in recalculate(original)] == [33, 33, 34]
    assert [o.weight for o in apply_action(original, OfferAction("add", 4))] == [25, 25, 25, 25]
    assert signature(original) == [
        (3, 2, "active", 34, False),
        (1, 0, "active", 33, False),
        (2, 1, "active", 33, False),
    ]
    assert recalculate(recalculate(original)) == recalculate(original)


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (offers(), []),
        (offers((8, 7, "removed", 0, False)), [(8, 7, "removed", 0, False)]),
        (
            offers(
                (1, 0, "active", 25, True), (2, 1, "active", 75, False), (3, 2, "removed", 0, False)
            ),
            [(1, 0, "active", 25, True), (2, 1, "active", 75, False), (3, 2, "removed", 0, False)],
        ),
        (
            offers(
                (1, 0, "active", 20, True),
                (2, 1, "active", 31, True),
                (3, 2, "active", 24, False),
                (4, 3, "active", 25, False),
            ),
            [
                (1, 0, "active", 20, True),
                (2, 1, "active", 31, True),
                (3, 2, "active", 24, False),
                (4, 3, "active", 25, False),
            ],
        ),
        (
            offers(
                (1, 0, "active", 99, True), (2, 1, "active", 1, False), (3, 2, "active", 0, False)
            ),
            [(1, 0, "active", 99, True), (2, 1, "active", 0, False), (3, 2, "active", 1, False)],
        ),
        (
            offers((2, 0, "active", 50, False), (1, 0, "active", 50, False)),
            [(1, 0, "active", 50, False), (2, 0, "active", 50, False)],
        ),
    ],
)
def test_recalculate_cases(
    snapshot: list[OfferWeight], expected: list[tuple[int, int, str, int, bool]]
) -> None:
    result = recalculate(snapshot)
    assert signature(result) == expected
    assert recalculate(list(reversed(snapshot))) == result


def test_add_in_empty_and_archival_stream() -> None:
    assert signature(apply_action([], OfferAction("add", 5))) == [(5, 0, "added", 100, False)]
    archived = offers((3, 7, "removed", 0, False))
    assert signature(apply_action(archived, OfferAction("add", 5))) == [
        (3, 7, "removed", 0, False),
        (5, 8, "added", 100, False),
    ]


def test_add_archived_id_is_bring_back_and_duplicate_rejected() -> None:
    source = offers((1, 0, "active", 100, False), (2, 2, "removed", 0, False))
    assert signature(apply_action(source, OfferAction("add", 2))) == [
        (1, 0, "active", 50, False),
        (2, 2, "active", 50, False),
    ]
    with pytest.raises(WeightRuleError):
        apply_action(source, OfferAction("add", 1))


def test_remove_pin_and_bring_back_transitions() -> None:
    source = offers(
        (1, 0, "active", 25, True), (2, 1, "active", 37, False), (3, 2, "active", 38, False)
    )
    removed = apply_action(source, OfferAction("remove", 1))
    assert signature(removed) == [
        (1, 0, "removed", 0, False),
        (2, 1, "active", 50, False),
        (3, 2, "active", 50, False),
    ]
    back = apply_action(removed, OfferAction("bring_back", 1))
    assert signature(back) == [
        (1, 0, "active", 33, False),
        (2, 1, "active", 33, False),
        (3, 2, "active", 34, False),
    ]
    assert source[0].pinned
    assert source[0].weight == 25
    assert apply_action(source, OfferAction("remove", 2))[0].weight == 25


def test_added_remove_disappears_and_single_active_can_be_removed() -> None:
    with_added = apply_action(offers((1, 0, "active", 100, False)), OfferAction("add", 2))
    assert signature(apply_action(with_added, OfferAction("remove", 2))) == [
        (1, 0, "active", 100, False)
    ]
    assert signature(
        apply_action(offers((1, 0, "active", 100, False)), OfferAction("remove", 1))
    ) == [(1, 0, "removed", 0, False)]


def test_cannot_remove_last_unpinned_added_offer_beside_pinned_active() -> None:
    snapshot = offers((1, 0, "active", 60, True), (2, 1, "added", 40, False))
    assert not can_remove(snapshot, 2)
    with pytest.raises(WeightRuleError):
        apply_action(snapshot, OfferAction("remove", 2))


def test_pin_remove_availability_and_transitions() -> None:
    snapshot = offers((1, 0, "active", 50, False), (2, 1, "active", 50, False))
    assert can_pin(snapshot, 1)
    pinned = apply_action(snapshot, OfferAction("pin", 1))
    assert signature(pinned) == [(1, 0, "active", 50, True), (2, 1, "active", 50, False)]
    assert apply_action(pinned, OfferAction("unpin", 1)) == snapshot
    assert can_remove(pinned, 1)
    assert not can_remove(pinned, 2)
    assert not can_pin(offers((1, 0, "active", 100, False)), 1)
    assert not can_pin(snapshot, 99)
    assert not can_pin(snapshot, True)
    assert not can_remove(snapshot, 99)
    assert not can_remove(snapshot, True)


@pytest.mark.parametrize(
    ("snapshot", "action"),
    [
        (offers((1, 0, "active", 50, True), (2, 1, "active", 50, False)), OfferAction("pin", 2)),
        (
            offers(
                (1, 0, "active", 99, True), (2, 1, "active", 1, False), (3, 2, "active", 0, False)
            ),
            OfferAction("pin", 2),
        ),
        (offers((1, 0, "active", 50, False), (2, 1, "active", 50, False)), OfferAction("pin", 99)),
        (offers((1, 0, "active", 50, True), (2, 1, "active", 50, False)), OfferAction("remove", 2)),
        (offers((1, 0, "active", 100, False)), OfferAction("unpin", 1)),
        (offers((1, 0, "active", 100, False)), OfferAction("bring_back", 1)),
        (offers((1, 0, "removed", 0, False)), OfferAction("remove", 1)),
        (offers(), OfferAction("pin", 1)),
    ],
)
def test_forbidden_operations(snapshot: list[OfferWeight], action: OfferAction) -> None:
    with pytest.raises(WeightRuleError):
        apply_action(snapshot, action)


@pytest.mark.parametrize(
    "snapshot",
    [
        offers((1, 0, "active", 100, True)),
        offers((1, 0, "active", 100, True), (2, 1, "active", 0, False)),
        offers((1, 0, "active", 100, False), (1, 1, "active", 0, False)),
        [OfferWeight(1, -1, "active", 100, False)],
        [OfferWeight(1, True, "active", 100, False)],
        [OfferWeight(1, 0, "active", True, False)],
        [OfferWeight(1, 0, "active", 101, False)],
        [OfferWeight(1, 0, "added", 100, True)],
        [OfferWeight(1, 0, "removed", 1, False)],
        [OfferWeight(1, 0, "removed", 0, True)],
        [OfferWeight(True, 0, "active", 100, False)],
        [OfferWeight(1, 0, "active", 100, 1)],  # type: ignore[arg-type]
        [OfferWeight(1, 0, "unknown", 100, False)],  # type: ignore[arg-type]
    ],
)
def test_invalid_snapshots_are_rejected_and_not_mutated(snapshot: list[OfferWeight]) -> None:
    before = signature(snapshot)
    with pytest.raises(WeightRuleError):
        recalculate(snapshot)
    with pytest.raises(WeightRuleError):
        validate_distribution(snapshot)
    with pytest.raises(WeightRuleError):
        can_pin(snapshot, 1)
    with pytest.raises(WeightRuleError):
        can_remove(snapshot, 1)
    with pytest.raises(WeightRuleError):
        apply_action(snapshot, OfferAction("add", 2))
    assert signature(snapshot) == before


def test_recalculate_accepts_intermediate_total_and_validate_rejects_it() -> None:
    snapshot = offers((1, 0, "active", 25, True), (2, 1, "active", 50, False))
    assert [item.weight for item in recalculate(snapshot)] == [25, 75]
    with pytest.raises(WeightRuleError):
        validate_distribution(snapshot)


def test_invalid_action_kind_and_id_are_rejected() -> None:
    snapshot = offers((1, 0, "active", 100, False))
    with pytest.raises(WeightRuleError):
        apply_action(snapshot, OfferAction("wat", 1))  # type: ignore[arg-type]
    with pytest.raises(WeightRuleError):
        apply_action(snapshot, OfferAction("add", cast(int, True)))
