"""Plain data objects passed between the database, the rewards maths and the server."""

from dataclasses import dataclass
from datetime import date

REWARD_KINDS = ("points", "cashback")
RATE_CHANNELS = ("any", "online", "offline")
PURCHASE_CHANNELS = ("online", "offline")


@dataclass(frozen=True)
class Card:
    """A credit card and how it earns by default.

    Earning is expressed in "blocks": every `block_inr` rupees spent earns
    `base_rate` reward units. A card giving 4 points per ₹150 has block_inr=150,
    base_rate=4. A 1% cashback card has block_inr=100, base_rate=1 and
    unit_value_inr=1.
    """

    id: int
    name: str
    issuer: str
    reward_kind: str
    base_rate: float
    block_inr: float
    whole_blocks: bool  # True when a partial block earns nothing (typical for points)
    unit_value_inr: float  # rupee value of one reward unit
    statement_day: int  # day of month the billing cycle starts (1-28)
    network: str | None = None
    excluded: frozenset[str] = frozenset()
    notes: str | None = None

    @property
    def unit_label(self) -> str:
        return "points" if self.reward_kind == "points" else "₹ cashback"

    @property
    def base_return_pct(self) -> float:
        return self.base_rate * self.unit_value_inr / self.block_inr * 100


@dataclass(frozen=True)
class EarnRate:
    """An accelerated rate that replaces the base rate when it matches a purchase.

    It matches on a category, a merchant name (case-insensitive substring), or
    both, optionally restricted to online or offline spends. `monthly_cap`
    limits how many units this rate can give per statement cycle; spend beyond
    the cap falls back to the card's base rate.
    """

    id: int
    card_id: int
    rate: float
    category: str | None = None
    merchant: str | None = None
    channel: str = "any"
    monthly_cap: float | None = None
    label: str | None = None

    def describe(self) -> str:
        if self.label:
            return self.label
        parts = []
        if self.merchant:
            parts.append(self.merchant)
        if self.category:
            parts.append(self.category)
        if self.channel != "any":
            parts.append(self.channel)
        return " / ".join(parts)


@dataclass(frozen=True)
class Purchase:
    id: int
    card_id: int
    card_name: str
    spent_on: date
    amount: float
    merchant: str
    category: str
    channel: str
    units: float
    value_inr: float
    rate_id: int | None = None
    note: str | None = None
