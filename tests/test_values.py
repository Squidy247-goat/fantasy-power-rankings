import math

import pytest

from fpr.config import ValueCurve
from fpr.core.values import value

CURVE = ValueCurve(ceiling=330.0, decay=51.0, floor=20.0)


def test_rank_one_is_the_ceiling():
    # ln(1) == 0, so the best player in the league is worth exactly the ceiling.
    assert value(1, CURVE) == pytest.approx(330.0)


def test_value_decreases_with_rank():
    ranks = [1, 2, 5, 10, 25, 50, 100]
    vals = [value(r, CURVE) for r in ranks]
    assert vals == sorted(vals, reverse=True)


def test_concavity_is_the_whole_point():
    """The gap from 1 to 20 must beat the gap from 200 to 320.

    Under raw rank differential it's 19 vs 120 and the deep bench wins, which
    is exactly the distortion this curve exists to undo.
    """
    top_gap = value(1, CURVE) - value(20, CURVE)
    deep_gap = value(200, CURVE) - value(320, CURVE)
    assert top_gap > deep_gap


def test_equal_ratios_give_equal_gaps():
    # A log curve means doubling the rank always costs the same, whether it's
    # 1 -> 2 or 100 -> 200.
    assert (value(1, CURVE) - value(2, CURVE)) == pytest.approx(
        value(100, CURVE) - value(200, CURVE)
    )


def test_floor_holds_deep_players_up():
    # Well past where the curve would have gone negative.
    assert value(10_000, CURVE) == pytest.approx(20.0)
    assert value(1_000_000, CURVE) == pytest.approx(20.0)


def test_never_below_floor():
    assert all(value(r, CURVE) >= CURVE.floor for r in range(1, 2000))


@pytest.mark.parametrize("rank", [1.0, 0.5, 0.0, -3.0, -250.0])
def test_ranks_at_or_below_one_clamp_to_ceiling(rank):
    """The simulation draws from a normal, so top players land at or below zero
    on some trials. ln() would raise or return nan; clamping keeps the trial."""
    assert value(rank, CURVE) == pytest.approx(330.0)


def test_matches_the_formula():
    for rank in (3, 17, 88, 140):
        assert value(rank, CURVE) == pytest.approx(
            max(20.0, 330.0 - 51.0 * math.log(rank))
        )


def test_curve_constants_are_respected():
    steeper = ValueCurve(ceiling=100.0, decay=10.0, floor=0.0)
    assert value(1, steeper) == pytest.approx(100.0)
    assert value(10, steeper) == pytest.approx(100.0 - 10.0 * math.log(10))
