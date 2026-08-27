"""Monthly Creator Network diamond-tier rules.

Tier comparisons are deliberately based on saved monthly snapshots.  A person
maintains a tier when this month's diamonds meet or exceed the tier they held
in the immediately preceding snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


TIER_LEVELS: tuple[tuple[str, int], ...] = (
    ("100K", 100_000),
    ("200K", 200_000),
    ("300K", 300_000),
    ("500K", 500_000),
    ("1M", 1_000_000),
    ("1.6M", 1_600_000),
    ("3M", 3_000_000),
    ("5M", 5_000_000),
    ("8M", 8_000_000),
    ("10M", 10_000_000),
)


@dataclass(frozen=True)
class TierStatus:
    current_tier: str | None
    previous_tier: str | None
    outcome: str


def diamond_amount(value: object) -> int | None:
    """Parse common Backstage diamond values such as ``1.6M`` or ``75,067``."""
    text = str(value).strip().upper().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("M"):
        text, multiplier = text[:-1], 1_000_000
    elif text.endswith("K"):
        text, multiplier = text[:-1], 1_000
    try:
        return int(Decimal(text) * multiplier)
    except InvalidOperation:
        return None


def tier_for(value: object) -> str | None:
    amount = diamond_amount(value)
    if amount is None:
        return None
    return next((name for name, minimum in reversed(TIER_LEVELS) if amount >= minimum), None)


def compare_months(current: object, previous: object) -> TierStatus:
    current_tier = tier_for(current)
    previous_tier = tier_for(previous)
    if previous_tier is None:
        outcome = "New tier" if current_tier else "Below first tier"
    elif current_tier is None:
        outcome = "Below prior tier"
    else:
        current_index = [name for name, _ in TIER_LEVELS].index(current_tier)
        previous_index = [name for name, _ in TIER_LEVELS].index(previous_tier)
        outcome = "Moved up" if current_index > previous_index else "Maintained" if current_index == previous_index else "Below prior tier"
    return TierStatus(current_tier, previous_tier, outcome)
