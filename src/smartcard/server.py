# Copyright (c) 2026 Shubham Kumar Sahoo. Licensed under the PolyForm Strict License 1.0.0 (see LICENSE).
"""SmartCard MCP server: exposes the Wallet as MCP tools, a resource and a prompt."""

import functools
import json
import threading
from collections.abc import Callable
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from smartcard import __version__
from smartcard.categories import DEFAULT_EXCLUSIONS, DESCRIPTIONS
from smartcard.db import default_db_path
from smartcard.models import Card, EarnRate, Purchase
from smartcard.rewards import Earning
from smartcard.wallet import Wallet

INSTRUCTIONS = """\
SmartCard (by Shubham Kumar Sahoo) tracks the user's credit cards and tells them
which card to use for each purchase.

- Amounts are in Indian rupees (INR).
- Start with list_cards. If the wallet is empty, help the user add a card with
  add_card and then add_earn_rate for each accelerated reward category.
- Earning model: every `block_inr` rupees earns `base_rate` units. Earn rates replace
  the base rate for matching categories or merchants and may have a monthly cap; spend
  past the cap earns the base rate.
- Never invent reward rates. Use search_card_terms, show the user what you found, and
  save only what they confirm.
- Ask the user before removing cards or purchases.
"""

mcp = MCPServer("smartcard", title="SmartCard", version=__version__, instructions=INSTRUCTIONS)

# Hints that let MCP clients decide when to ask the user before running a tool.
READ_ONLY = ToolAnnotations(read_only_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DELETES = ToolAnnotations(read_only_hint=False, destructive_hint=True)
WEB = ToolAnnotations(read_only_hint=True, open_world_hint=True)

# Sync tools run in worker threads and share one SQLite connection.
_lock = threading.Lock()


def tool(annotations: ToolAnnotations) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a tool. Validation errors from the wallet become ToolErrors, so the
    assistant sees the message (e.g. "No card matches 'gold'") and can recover."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _lock:
                try:
                    return fn(*args, **kwargs)
                except ValueError as exc:
                    raise ToolError(str(exc)) from exc

        return mcp.tool(annotations=annotations)(wrapper)

    return register

_wallet: Wallet | None = None

CardRef = Annotated[str, Field(description="Card id, or its name (a unique part is enough)")]
CategoryArg = Annotated[
    str | None,
    Field(description="Spending category, see the smartcard://categories resource. "
                      "Leave empty to guess from a well-known merchant."),
]


def get_wallet() -> Wallet:
    global _wallet
    if _wallet is None:
        _wallet = Wallet.open(default_db_path())
    return _wallet


def use_wallet(wallet: Wallet | None) -> None:
    """Swap the wallet the tools use (tests point this at an in-memory database)."""
    global _wallet
    _wallet = wallet


# ---------------------------------------------------------------- formatting


def _card_view(card: Card) -> dict[str, Any]:
    return {
        "id": card.id,
        "name": card.name,
        "issuer": card.issuer,
        "network": card.network,
        "reward_kind": card.reward_kind,
        "base_earning": f"{card.base_rate:g} {card.unit_label} per ₹{card.block_inr:g}",
        "base_return_pct": round(card.base_return_pct, 2),
        "unit_value_inr": card.unit_value_inr,
        "statement_day": card.statement_day,
        "excluded_categories": sorted(card.excluded),
        "notes": card.notes,
    }


def _rate_view(rate: EarnRate, card: Card) -> dict[str, Any]:
    return {
        "id": rate.id,
        "label": rate.describe(),
        "earning": f"{rate.rate:g} {card.unit_label} per ₹{card.block_inr:g}",
        "return_pct": round(rate.rate * card.unit_value_inr / card.block_inr * 100, 2),
        "category": rate.category,
        "merchant": rate.merchant,
        "channel": rate.channel,
        "monthly_cap": rate.monthly_cap,
    }


def _purchase_view(p: Purchase) -> dict[str, Any]:
    return {
        "id": p.id,
        "date": p.spent_on.isoformat(),
        "card": p.card_name,
        "amount": p.amount,
        "merchant": p.merchant,
        "category": p.category,
        "channel": p.channel,
        "rewards": p.units,
        "rewards_inr": p.value_inr,
        "note": p.note,
    }


def _earning_view(card: Card, e: Earning) -> dict[str, Any]:
    return {
        "card": card.name,
        "card_id": card.id,
        "earns": f"{e.units:g} {card.unit_label}",
        "value_inr": e.value_inr,
        "return_pct": e.return_pct,
        "rate_used": e.rate.describe() if e.rate else None,
        "how": list(e.steps),
    }


# --------------------------------------------------------------------- cards


@tool(READ_ONLY)
def list_cards() -> dict[str, Any]:
    """List every card in the wallet with its base earning rate."""
    cards = get_wallet().list_cards()
    return {"count": len(cards), "cards": [_card_view(c) for c in cards]}


@tool(READ_ONLY)
def card_details(card: CardRef) -> dict[str, Any]:
    """Everything about one card: base rate, accelerated earn rates, how much of each
    monthly cap is used in the current statement cycle, and its reward balance."""
    wallet = get_wallet()
    c = wallet.get_card(card)
    balance = next(b for b in wallet.balances() if b.card.id == c.id)
    return {
        **_card_view(c),
        "earn_rates": [_rate_view(r, c) for r in wallet.rates_for(c.id)],
        "caps_this_cycle": [
            {
                "rate": s.rate.describe(),
                "cap": s.rate.monthly_cap,
                "used": s.used,
                "left": s.left,
                "cycle": f"{s.cycle_start} to {s.cycle_end - timedelta(days=1)}",
            }
            for s in wallet.cap_status(c)
        ],
        "balance": {"units": balance.balance, "value_inr": balance.value_inr},
        "recent_adjustments": wallet.adjustments_for(c.id, limit=5),
    }


@tool(WRITES)
def add_card(
    name: Annotated[str, Field(description="Card name as the user calls it, e.g. 'HDFC Millennia'")],
    issuer: Annotated[str, Field(description="Issuing bank")],
    reward_kind: Literal["points", "cashback"],
    base_rate: Annotated[
        float,
        Field(ge=0, description="Units earned per block_inr on regular spends. "
                                "1% cashback = 1 with block_inr 100; '4 points per ₹150' = 4."),
    ],
    block_inr: Annotated[
        float, Field(gt=0, description="Spend (₹) that earns base_rate units, e.g. 150")
    ] = 100,
    unit_value_inr: Annotated[
        float | None,
        Field(ge=0, description="Rupee value of one point. Required for points cards; "
                                "cashback defaults to 1."),
    ] = None,
    statement_day: Annotated[
        int, Field(ge=1, le=28, description="Day of the month the billing cycle starts")
    ] = 1,
    network: Annotated[str | None, Field(description="Visa, Mastercard, RuPay, Amex, Diners")] = None,
    excluded_categories: Annotated[
        list[str] | None,
        Field(description="Categories that earn nothing on this card. Defaults to "
                          + ", ".join(sorted(c.value for c in DEFAULT_EXCLUSIONS))),
    ] = None,
    notes: Annotated[str | None, Field(description="Fees, perks, anything worth remembering")] = None,
    confirmed: Annotated[
        bool,
        Field(description="Set true only after the user has seen and agreed to these terms. "
                          "Without it the card is previewed, not saved."),
    ] = False,
) -> dict[str, Any]:
    """Add a credit card to the wallet, then call add_earn_rate for each category or
    merchant where it earns more than its base rate.

    Wrong terms silently skew every later recommendation, so saving needs the user's
    agreement: call once without `confirmed` to get a preview, show it to the user
    (including anything found with search_card_terms and their statement day), and
    call again with confirmed=true only after they agree."""
    if not confirmed:
        unit = "points" if reward_kind == "points" else "₹ cashback"
        return {
            "saved": False,
            "proposed": {
                "name": name, "issuer": issuer, "network": network,
                "reward_kind": reward_kind,
                "base_earning": f"{base_rate:g} {unit} per ₹{block_inr:g}",
                "unit_value_inr": unit_value_inr if unit_value_inr is not None
                else (1.0 if reward_kind == "cashback" else None),
                "statement_day": statement_day,
                "excluded_categories": sorted(excluded_categories)
                if excluded_categories is not None
                else sorted(c.value for c in DEFAULT_EXCLUSIONS),
                "notes": notes,
            },
            "next_step": "Show these terms to the user. Call again with confirmed=true "
                         "once they agree, or with corrected values.",
        }
    c = get_wallet().add_card(
        name=name, issuer=issuer, reward_kind=reward_kind, base_rate=base_rate,
        block_inr=block_inr, unit_value_inr=unit_value_inr, statement_day=statement_day,
        network=network, excluded_categories=excluded_categories, notes=notes,
    )
    return {"added": _card_view(c)}


@tool(WRITES)
def update_card(
    card: CardRef,
    name: str | None = None,
    issuer: str | None = None,
    base_rate: Annotated[float | None, Field(ge=0)] = None,
    block_inr: Annotated[float | None, Field(gt=0)] = None,
    unit_value_inr: Annotated[float | None, Field(ge=0)] = None,
    statement_day: Annotated[int | None, Field(ge=1, le=28)] = None,
    network: str | None = None,
    excluded_categories: Annotated[
        list[str] | None, Field(description="Replaces the whole exclusion list")
    ] = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Change details of an existing card. Only the fields you pass are changed."""
    changes = {
        k: v
        for k, v in {
            "name": name, "issuer": issuer, "base_rate": base_rate, "block_inr": block_inr,
            "unit_value_inr": unit_value_inr, "statement_day": statement_day,
            "network": network, "excluded_categories": excluded_categories, "notes": notes,
        }.items()
        if v is not None
    }
    if not changes:
        raise ValueError("Nothing to change: pass at least one field")
    return {"updated": _card_view(get_wallet().update_card(card, **changes))}


@tool(DELETES)
def remove_card(
    card: CardRef,
    confirm: Annotated[bool, Field(description="Must be true to actually delete")] = False,
) -> dict[str, Any]:
    """Delete a card and all its earn rates, purchases and point adjustments.
    Call once without confirm to see what would be deleted, then ask the user."""
    wallet = get_wallet()
    c = wallet.get_card(card)
    if not confirm:
        count = len(wallet.list_purchases(card=c.id, limit=1_000_000))
        return {
            "deleted": False,
            "would_delete": {"card": c.name, "purchases": count,
                             "earn_rates": len(wallet.rates_for(c.id))},
            "next_step": "Ask the user, then call again with confirm=true",
        }
    wallet.remove_card(c.id)
    return {"deleted": True, "card": c.name}


@tool(WRITES)
def add_earn_rate(
    card: CardRef,
    rate: Annotated[
        float, Field(ge=0, description="Units per block_inr of the card when this rate applies "
                                       "(replaces the base rate, it is not added to it)")
    ],
    category: CategoryArg = None,
    merchant: Annotated[
        str | None, Field(description="Merchant name to match, e.g. 'Swiggy' (case-insensitive)")
    ] = None,
    channel: Literal["any", "online", "offline"] = "any",
    monthly_cap: Annotated[
        float | None,
        Field(gt=0, description="Max units this rate can earn per statement cycle"),
    ] = None,
    label: Annotated[str | None, Field(description="Short name, e.g. '5% on Amazon'")] = None,
) -> dict[str, Any]:
    """Add an accelerated reward rate to a card for a category, a merchant, or both.
    When several rates match a purchase, the most specific one wins
    (merchant beats category). Only add rates the user has confirmed."""
    wallet = get_wallet()
    r = wallet.add_rate(card, rate=rate, category=category, merchant=merchant,
                        channel=channel, monthly_cap=monthly_cap, label=label)
    return {"added": _rate_view(r, wallet.get_card(r.card_id))}


@tool(DELETES)
def remove_earn_rate(rate_id: int) -> dict[str, Any]:
    """Delete one earn rate (ids are shown by card_details)."""
    r = get_wallet().remove_rate(rate_id)
    return {"removed": r.describe(), "rate_id": r.id}


# ---------------------------------------------------------- recommendations


@tool(READ_ONLY)
def recommend_card(
    amount: Annotated[float, Field(gt=0, description="Purchase amount in ₹")],
    merchant: Annotated[str, Field(description="Where the user is buying, e.g. 'Amazon'")],
    category: CategoryArg = None,
    channel: Literal["online", "offline"] = "offline",
) -> dict[str, Any]:
    """Rank every card in the wallet by the rupee value it would give back on this
    purchase, taking monthly caps already used into account."""
    cat, ranking = get_wallet().recommend(
        amount=amount, merchant=merchant, category=category, channel=channel
    )
    best_card, best = ranking[0]
    result = {
        "purchase": {"amount": amount, "merchant": merchant, "category": cat, "channel": channel},
        "best": _earning_view(best_card, best),
        "ranking": [_earning_view(c, e) for c, e in ranking],
    }
    if best.value_inr == 0:
        result["note"] = f"None of your cards earn rewards on '{cat}' spends."
    return result


# ---------------------------------------------------------------- purchases


@tool(WRITES)
def log_purchase(
    card: CardRef,
    amount: Annotated[float, Field(gt=0, description="Amount in ₹")],
    merchant: str,
    category: CategoryArg = None,
    channel: Literal["online", "offline"] = "offline",
    spent_on: Annotated[date | None, Field(description="YYYY-MM-DD, defaults to today")] = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record a purchase made on a card and calculate the rewards it earned. Also says
    whether another card in the wallet would have earned more."""
    wallet = get_wallet()
    _, ranking = wallet.recommend(amount=amount, merchant=merchant, category=category,
                                  channel=channel, on=spent_on)
    purchase, earning = wallet.log_purchase(
        card, amount=amount, merchant=merchant, category=category, channel=channel,
        spent_on=spent_on, note=note,
    )
    result = {"logged": _purchase_view(purchase), "how": list(earning.steps)}
    best_card, best = ranking[0]
    if best_card.id != purchase.card_id and best.value_inr > earning.value_inr:
        result["better_option"] = {
            "card": best_card.name,
            "value_inr": best.value_inr,
            "missed_inr": round(best.value_inr - earning.value_inr, 2),
        }
    return result


@tool(READ_ONLY)
def list_purchases(
    card: Annotated[str | None, Field(description="Card id or name")] = None,
    category: str | None = None,
    merchant: Annotated[str | None, Field(description="Part of the merchant name")] = None,
    start: Annotated[date | None, Field(description="From this date (YYYY-MM-DD), inclusive")] = None,
    end: Annotated[date | None, Field(description="Up to this date (YYYY-MM-DD), inclusive")] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 20,
) -> dict[str, Any]:
    """Show purchases, newest first, optionally filtered."""
    purchases = get_wallet().list_purchases(
        card=card, category=category, merchant=merchant, start=start, end=end, limit=limit
    )
    return {"count": len(purchases), "purchases": [_purchase_view(p) for p in purchases]}


@tool(DELETES)
def delete_purchase(purchase_id: int) -> dict[str, Any]:
    """Delete a purchase logged by mistake. Ask the user first."""
    p = get_wallet().delete_purchase(purchase_id)
    return {"deleted": _purchase_view(p)}


# ------------------------------------------------------------------- points


@tool(READ_ONLY)
def rewards_balance(card: Annotated[str | None, Field(description="Card id or name")] = None) -> dict[str, Any]:
    """Reward balance per card: earned from purchases, plus or minus manual adjustments,
    and what the balance is worth in rupees."""
    wallet = get_wallet()
    balances = wallet.balances()
    if card is not None:
        wanted = wallet.get_card(card).id
        balances = [b for b in balances if b.card.id == wanted]
    return {
        "cards": [
            {
                "card": b.card.name,
                "unit": b.card.unit_label,
                "earned": b.earned,
                "adjustments": b.adjusted,
                "balance": b.balance,
                "value_inr": b.value_inr,
            }
            for b in balances
        ],
        "total_value_inr": round(sum(b.value_inr for b in balances), 2),
    }


@tool(WRITES)
def adjust_rewards(
    card: CardRef,
    units: Annotated[
        float, Field(description="Positive to add (welcome bonus), negative to subtract "
                                 "(redemption, expiry)")
    ],
    note: Annotated[str, Field(description="What happened, e.g. 'Redeemed for Taj voucher'")],
    value_inr: Annotated[float | None, Field(description="Rupee value received, if redeemed")] = None,
    on: Annotated[date | None, Field(description="YYYY-MM-DD, defaults to today")] = None,
) -> dict[str, Any]:
    """Record points or cashback that did not come from a purchase."""
    return {"recorded": get_wallet().add_adjustment(
        card, units=units, note=note, value_inr=value_inr, made_on=on
    )}


@tool(READ_ONLY)
def spending_report(
    start: Annotated[date | None, Field(description="YYYY-MM-DD, inclusive")] = None,
    end: Annotated[date | None, Field(description="YYYY-MM-DD, inclusive")] = None,
) -> dict[str, Any]:
    """Spending and rewards by category and by card. With no dates, covers the
    current calendar month."""
    if start is None and end is None:
        today = date.today()
        start, end = today.replace(day=1), today
    return get_wallet().spending_report(start=start, end=end)


# -------------------------------------------------------------------- search


@tool(WEB)
def search_card_terms(
    card_name: Annotated[str, Field(description="e.g. 'HDFC Millennia'")],
    topic: Annotated[str, Field(description="What to look for")] = "reward rate cashback cap",
    max_results: Annotated[int, Field(ge=1, le=15)] = 6,
) -> dict[str, Any]:
    """Search the web for a card's published reward terms. Results are raw snippets:
    summarise them for the user and confirm before saving anything."""
    from ddgs import DDGS

    query = f"{card_name} credit card {topic}"
    try:
        results = DDGS().text(query, region="in-en", max_results=max_results)
    except Exception as exc:  # network errors, rate limits
        raise ValueError(f"Web search failed: {exc}") from exc
    return {
        "query": query,
        "results": [
            {"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
            for r in results
        ],
    }


# --------------------------------------------------------- resource & prompt


@mcp.resource("smartcard://categories", mime_type="application/json")
def categories() -> str:
    """Spending categories SmartCard understands."""
    return json.dumps(
        {
            "categories": {c.value: d for c, d in DESCRIPTIONS.items()},
            "excluded_by_default": sorted(c.value for c in DEFAULT_EXCLUSIONS),
        },
        indent=2,
    )


@mcp.prompt()
def set_up_card(card_name: str) -> str:
    """Walk the user through adding a card with correct reward rates."""
    return (
        f"Help me add my '{card_name}' credit card to SmartCard.\n"
        "1. Use search_card_terms to find its base reward rate, accelerated categories, "
        "monthly caps, excluded categories and point value.\n"
        "2. Summarise what you found in a short table and ask me to confirm or correct it, "
        "including my statement date.\n"
        "3. Once I confirm, call add_card, then add_earn_rate for each accelerated rate.\n"
        "4. Finish with card_details so I can check the result."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
