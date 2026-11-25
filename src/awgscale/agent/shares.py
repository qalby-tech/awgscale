"""The exits' shares of the users, as arithmetic."""
from __future__ import annotations

from typing import Sequence

WHOLE = 100
SMALLEST_SHARE = 1


def shares_percent(weights: Sequence[int]) -> list[int]:
    """Weights → whole percents that add up to 100 (the largest takes what rounding leaves)."""
    total = sum(weights)
    if not weights or total <= 0:
        return [0 for _ in weights]
    percents = [round(WHOLE * weight / total) for weight in weights]
    largest = max(range(len(percents)), key=lambda index: weights[index])
    percents[largest] += WHOLE - sum(percents)
    return percents


def shares_with(weights: Sequence[int], percent: int) -> tuple[list[int], int]:
    """The shares of the exits there are, once a new one takes `percent`: they keep their
    proportion inside what is left. → (their new weights, the new one's) — whole numbers that add up to 100."""
    left = WHOLE - percent
    total = sum(weights) or 1
    # an exit switched off (0) stays off; the others keep at least the smallest share
    shares = [0 if weight == 0 else max(SMALLEST_SHARE, round(left * weight / total)) for weight in weights]
    if shares:
        largest = max(range(len(shares)), key=lambda index: weights[index])
        remainder = left - sum(shares)
        if shares[largest] + remainder >= SMALLEST_SHARE:       # what rounding leaves goes to the largest — never down to nothing
            shares[largest] += remainder
    return shares, percent
