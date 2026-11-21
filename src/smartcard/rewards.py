"""Reward maths. Pure functions: no database, no I/O, easy to test."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from smartcard.models import Card, EarnRate


@dataclass(frozen=True)
class Earning:
    """What one purchase earns on one card, and why."""

    amount: float
    units: float
    value_inr: float
    rate_units: float  # units that count against the matched rate's monthly cap
    rate: EarnRate | None
    steps: tuple[str, ...]

    @property
    def return_pct(self) -> float:
        return round(self.value_inr / self.amount * 100, 2) if self.amount else 0.0


def statement_cycle(statement_day: int, on: date) -> tuple[date, date]:
    """Return the [start, end) dates of the billing cycle that contains `on`."""
    if on.day >= statement_day:
        start = date(on.year, on.month, statement_day)
    elif on.month == 1:
        start = date(on.year - 1, 12, statement_day)
    else:
        start = date(on.year, on.month - 1, statement_day)
    if start.month == 12:
        end = date(start.year + 1, 1, statement_day)
    else:
        end = date(start.year, start.month + 1, statement_day)
    return start, end


def spend_blocks(card: Card, amount: float) -> float:
    blocks = amount / card.block_inr
    # Small epsilon so ₹300 / ₹150 counts as exactly 2 blocks despite float error.
    return math.floor(blocks + 1e-9) if card.whole_blocks else blocks


def _matches(rate: EarnRate, merchant: str, category: str, channel: str) -> bool:
    if rate.merchant and rate.merchant.lower() not in merchant.lower():
        return False
    if rate.category and rate.category != category:
        return False
    if rate.channel != "any" and rate.channel != channel:
        return False
    return True


def _specificity(rate: EarnRate) -> float:
    # A merchant rule beats a category rule; a channel restriction breaks ties.
    return 2 * bool(rate.merchant) + bool(rate.category) + 0.5 * (rate.channel != "any")


def pick_rate(
    rates: Sequence[EarnRate], merchant: str, category: str, channel: str
) -> EarnRate | None:
    """The most specific matching rate, preferring the higher rate on a tie."""
    matching = [r for r in rates if _matches(r, merchant, category, channel)]
    if not matching:
        return None
    return max(matching, key=lambda r: (_specificity(r), r.rate))


def compute_earning(
    card: Card,
    rates: Sequence[EarnRate],
    *,
    amount: float,
    merchant: str,
    category: str,
    channel: str,
    cap_used: Mapping[int, float],
) -> Earning:
    """Work out the reward for a purchase.

    `cap_used` maps rate id -> units already earned from that rate in the
    current statement cycle.
    """
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")

    if category in card.excluded:
        return Earning(
            amount, 0.0, 0.0, 0.0, None,
            (f"'{category}' spends earn nothing on {card.name}",),
        )

    blocks = spend_blocks(card, amount)
    rate = pick_rate(rates, merchant, category, channel)
    unit = card.unit_label
    steps: list[str] = []

    if rate is None:
        units = blocks * card.base_rate
        rate_units = 0.0
        steps.append(
            f"Base rate: {card.base_rate:g} per ₹{card.block_inr:g} → {units:.2f} {unit}"
        )
    else:
        wanted = blocks * rate.rate
        units = rate_units = wanted
        steps.append(
            f"Accelerated rate '{rate.describe()}': {rate.rate:g} per ₹{card.block_inr:g}"
            f" → {wanted:.2f} {unit}"
        )
        if rate.monthly_cap is not None:
            left = max(0.0, rate.monthly_cap - cap_used.get(rate.id, 0.0))
            if wanted > left:
                spill_blocks = (wanted - left) / rate.rate
                rate_units = left
                units = left + spill_blocks * card.base_rate
                steps.append(
                    f"Monthly cap of {rate.monthly_cap:g} leaves {left:.2f}; the rest earns"
                    f" the base rate → {units:.2f} {unit} in total"
                )

    units = round(units, 2)
    return Earning(
        amount=amount,
        units=units,
        value_inr=round(units * card.unit_value_inr, 2),
        rate_units=round(rate_units, 2),
        rate=rate,
        steps=tuple(steps),
    )
