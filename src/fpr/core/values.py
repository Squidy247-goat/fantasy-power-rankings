"""Turning a rank into a number you can subtract.

Margin of victory needs a scale, and raw rank differential is the wrong one.
Under rank differential the gap between the 200th and 320th player (120) looks
larger than the gap between the 1st and the 20th (19), which is backwards from
how fantasy scoring actually behaves -- it falls off a cliff at the top and
flattens out in the deep bench where everyone is roughly interchangeable.

A logarithm has exactly that shape, so:

    value(rank) = max(floor, ceiling - decay * ln(rank))

The floor keeps deep bench players from going negative and dragging a team's
point differential around based on who happens to roster the 300th-best player.
"""

from __future__ import annotations

import math

from fpr.config import ValueCurve


def value(rank: float, curve: ValueCurve) -> float:
    """Value of a player at the given consensus rank.

    Ranks below 1 are clamped rather than rejected -- the simulation draws
    ranks from a normal distribution and the top few players will land at 0 or
    negative on some trials, where ln() would blow up.
    """
    return max(curve.floor, curve.ceiling - curve.decay * math.log(max(1.0, rank)))


def values(ranks, curve: ValueCurve) -> list[float]:
    """value() over a sequence, for the simulation's inner loop."""
    return [value(r, curve) for r in ranks]
