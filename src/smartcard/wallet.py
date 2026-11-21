"""The Wallet: every SmartCard operation, on top of SQLite.

The MCP server is a thin layer over this class, so everything here can be
used (and tested) without an MCP client.
"""

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from smartcard import db
from smartcard.categories import DEFAULT_EXCLUSIONS, guess_category, parse_category
from smartcard.models import (
    PURCHASE_CHANNELS,
    RATE_CHANNELS,
    REWARD_KINDS,
    Card,
    EarnRate,
    Purchase,
)
from smartcard.rewards import Earning, compute_earning, statement_cycle

_PURCHASE_SELECT = """
    SELECT p.*, c.name AS card_name, c.unit_value_inr
    FROM purchases p JOIN cards c ON c.id = p.card_id
"""


@dataclass(frozen=True)
class Balance:
    card: Card
    earned: float
    adjusted: float

    @property
    def balance(self) -> float:
        return round(self.earned + self.adjusted, 2)

    @property
    def value_inr(self) -> float:
        return round(self.balance * self.card.unit_value_inr, 2)


@dataclass(frozen=True)
class CapStatus:
    rate: EarnRate
    used: float
    cycle_start: date
    cycle_end: date

    @property
    def left(self) -> float:
        return round(max(0.0, (self.rate.monthly_cap or 0) - self.used), 2)


def resolve_category(category: str | None, merchant: str) -> str:
    """Use the given category, or guess it from a well-known merchant name."""
    if category:
        return parse_category(category).value
    guessed = guess_category(merchant)
    if guessed is None:
        raise ValueError(
            f"Couldn't work out the category for '{merchant}'. Pass a category explicitly."
        )
    return guessed.value


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _validate_card(card: Card) -> None:
    if not card.name:
        raise ValueError("Card name can't be empty")
    if not card.issuer:
        raise ValueError("Issuer can't be empty")
    if card.reward_kind not in REWARD_KINDS:
        raise ValueError(f"reward_kind must be one of {REWARD_KINDS}")
    if card.base_rate < 0:
        raise ValueError("base_rate can't be negative")
    if card.block_inr <= 0:
        raise ValueError("block_inr must be greater than zero")
    if card.unit_value_inr < 0:
        raise ValueError("unit_value_inr can't be negative")
    if not 1 <= card.statement_day <= 28:
        raise ValueError("statement_day must be between 1 and 28")


def _check_channel(channel: str, allowed: tuple[str, ...]) -> None:
    if channel not in allowed:
        raise ValueError(f"channel must be one of {allowed}")


class Wallet:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @classmethod
    def open(cls, path: str | Path) -> "Wallet":
        return cls(db.connect(path))

    # ------------------------------------------------------------------ cards

    def add_card(
        self,
        *,
        name: str,
        issuer: str,
        reward_kind: str,
        base_rate: float,
        block_inr: float = 100.0,
        unit_value_inr: float | None = None,
        whole_blocks: bool | None = None,
        statement_day: int = 1,
        network: str | None = None,
        excluded_categories: list[str] | None = None,
        notes: str | None = None,
    ) -> Card:
        if unit_value_inr is None:
            if reward_kind == "points":
                raise ValueError(
                    "unit_value_inr is required for points cards (rupee value of one point)"
                )
            unit_value_inr = 1.0
        if excluded_categories is None:
            excluded = frozenset(c.value for c in DEFAULT_EXCLUSIONS)
        else:
            excluded = frozenset(parse_category(c).value for c in excluded_categories)
        card = Card(
            id=0,
            name=name.strip(),
            issuer=issuer.strip(),
            reward_kind=reward_kind,
            base_rate=base_rate,
            block_inr=block_inr,
            whole_blocks=reward_kind == "points" if whole_blocks is None else whole_blocks,
            unit_value_inr=unit_value_inr,
            statement_day=statement_day,
            network=network,
            excluded=excluded,
            notes=notes,
        )
        _validate_card(card)
        try:
            with self.conn:
                cur = self.conn.execute(
                    """INSERT INTO cards (name, issuer, network, reward_kind, base_rate,
                           block_inr, whole_blocks, unit_value_inr, statement_day, excluded, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        card.name, card.issuer, card.network, card.reward_kind,
                        card.base_rate, card.block_inr, int(card.whole_blocks),
                        card.unit_value_inr, card.statement_day,
                        json.dumps(sorted(card.excluded)), card.notes,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A card named '{card.name}' already exists") from exc
        return self.get_card(cur.lastrowid)

    def update_card(self, ref: int | str, **changes) -> Card:
        """Change any card field; `excluded_categories` replaces the exclusion list."""
        card = self.get_card(ref)
        if "excluded_categories" in changes:
            cats = changes.pop("excluded_categories") or []
            changes["excluded"] = frozenset(parse_category(c).value for c in cats)
        unknown = set(changes) - (set(Card.__dataclass_fields__) - {"id"})
        if unknown:
            raise ValueError(f"Can't update: {', '.join(sorted(unknown))}")
        updated = replace(card, **changes)
        _validate_card(updated)
        try:
            with self.conn:
                self.conn.execute(
                    """UPDATE cards SET name=?, issuer=?, network=?, reward_kind=?, base_rate=?,
                           block_inr=?, whole_blocks=?, unit_value_inr=?, statement_day=?,
                           excluded=?, notes=?
                       WHERE id=?""",
                    (
                        updated.name, updated.issuer, updated.network, updated.reward_kind,
                        updated.base_rate, updated.block_inr, int(updated.whole_blocks),
                        updated.unit_value_inr, updated.statement_day,
                        json.dumps(sorted(updated.excluded)), updated.notes, card.id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"A card named '{updated.name}' already exists") from exc
        return self.get_card(card.id)

    def get_card(self, ref: int | str) -> Card:
        """Find a card by id, exact name, or a unique part of its name."""
        if isinstance(ref, int) or str(ref).strip().isdigit():
            row = self.conn.execute("SELECT * FROM cards WHERE id = ?", (int(ref),)).fetchone()
            if row is None:
                raise ValueError(f"No card with id {ref}")
            return db.card_from_row(row)

        text = str(ref).strip()
        row = self.conn.execute("SELECT * FROM cards WHERE name = ?", (text,)).fetchone()
        if row is not None:
            return db.card_from_row(row)
        rows = self.conn.execute(
            "SELECT * FROM cards WHERE name LIKE ? ESCAPE '\\' ORDER BY name", (_like(text),)
        ).fetchall()
        if len(rows) == 1:
            return db.card_from_row(rows[0])
        if rows:
            names = ", ".join(r["name"] for r in rows)
            raise ValueError(f"'{ref}' matches several cards ({names}). Be more specific.")
        names = ", ".join(c.name for c in self.list_cards()) or "none yet"
        raise ValueError(f"No card matches '{ref}'. Your cards: {names}")

    def list_cards(self) -> list[Card]:
        rows = self.conn.execute("SELECT * FROM cards ORDER BY name").fetchall()
        return [db.card_from_row(r) for r in rows]

    def remove_card(self, ref: int | str) -> Card:
        """Delete a card together with its rates, purchases and adjustments."""
        card = self.get_card(ref)
        with self.conn:
            self.conn.execute("DELETE FROM cards WHERE id = ?", (card.id,))
        return card

    # ------------------------------------------------------------------ rates

    def add_rate(
        self,
        card_ref: int | str,
        *,
        rate: float,
        category: str | None = None,
        merchant: str | None = None,
        channel: str = "any",
        monthly_cap: float | None = None,
        label: str | None = None,
    ) -> EarnRate:
        card = self.get_card(card_ref)
        merchant = merchant.strip() if merchant else None
        if not category and not merchant:
            raise ValueError("An earn rate needs a category, a merchant, or both")
        cat = parse_category(category).value if category else None
        _check_channel(channel, RATE_CHANNELS)
        if rate < 0:
            raise ValueError("rate can't be negative")
        if monthly_cap is not None and monthly_cap <= 0:
            raise ValueError("monthly_cap must be greater than zero (or left out for no cap)")
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO earn_rates (card_id, rate, category, merchant, channel,
                       monthly_cap, label) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (card.id, rate, cat, merchant, channel, monthly_cap, label),
            )
        return self._get_rate(cur.lastrowid)

    def remove_rate(self, rate_id: int) -> EarnRate:
        rate = self._get_rate(rate_id)
        with self.conn:
            self.conn.execute("DELETE FROM earn_rates WHERE id = ?", (rate_id,))
        return rate

    def rates_for(self, card_id: int) -> list[EarnRate]:
        rows = self.conn.execute(
            "SELECT * FROM earn_rates WHERE card_id = ? ORDER BY id", (card_id,)
        ).fetchall()
        return [db.rate_from_row(r) for r in rows]

    def _get_rate(self, rate_id: int) -> EarnRate:
        row = self.conn.execute("SELECT * FROM earn_rates WHERE id = ?", (rate_id,)).fetchone()
        if row is None:
            raise ValueError(f"No earn rate with id {rate_id}")
        return db.rate_from_row(row)

    # ------------------------------------------------------------- caps/quotes

    def cap_usage(self, card: Card, on: date) -> dict[int, float]:
        """Units already earned per capped rate in the statement cycle containing `on`."""
        start, end = statement_cycle(card.statement_day, on)
        rows = self.conn.execute(
            """SELECT rate_id, SUM(rate_units) AS used FROM purchases
               WHERE card_id = ? AND rate_id IS NOT NULL AND spent_on >= ? AND spent_on < ?
               GROUP BY rate_id""",
            (card.id, start.isoformat(), end.isoformat()),
        ).fetchall()
        return {r["rate_id"]: r["used"] for r in rows}

    def cap_status(self, card: Card, on: date | None = None) -> list[CapStatus]:
        on = on or date.today()
        start, end = statement_cycle(card.statement_day, on)
        usage = self.cap_usage(card, on)
        return [
            CapStatus(rate=r, used=round(usage.get(r.id, 0.0), 2), cycle_start=start, cycle_end=end)
            for r in self.rates_for(card.id)
            if r.monthly_cap is not None
        ]

    def quote(
        self, card: Card, *, amount: float, merchant: str, category: str, channel: str, on: date
    ) -> Earning:
        return compute_earning(
            card,
            self.rates_for(card.id),
            amount=amount,
            merchant=merchant,
            category=category,
            channel=channel,
            cap_used=self.cap_usage(card, on),
        )

    def recommend(
        self,
        *,
        amount: float,
        merchant: str,
        category: str | None = None,
        channel: str = "offline",
        on: date | None = None,
    ) -> tuple[str, list[tuple[Card, Earning]]]:
        """Rank every card by the rupee value it would return. Returns (category, ranking)."""
        cards = self.list_cards()
        if not cards:
            raise ValueError("Your wallet is empty. Add a card first.")
        _check_channel(channel, PURCHASE_CHANNELS)
        cat = resolve_category(category, merchant)
        on = on or date.today()
        ranking = [
            (card, self.quote(card, amount=amount, merchant=merchant, category=cat,
                              channel=channel, on=on))
            for card in cards
        ]
        ranking.sort(key=lambda item: (-item[1].value_inr, item[0].name.lower()))
        return cat, ranking

    # -------------------------------------------------------------- purchases

    def log_purchase(
        self,
        card_ref: int | str,
        *,
        amount: float,
        merchant: str,
        category: str | None = None,
        channel: str = "offline",
        spent_on: date | None = None,
        note: str | None = None,
    ) -> tuple[Purchase, Earning]:
        card = self.get_card(card_ref)
        merchant = merchant.strip()
        if not merchant:
            raise ValueError("Merchant can't be empty")
        cat = resolve_category(category, merchant)
        _check_channel(channel, PURCHASE_CHANNELS)
        on = spent_on or date.today()
        earning = self.quote(card, amount=amount, merchant=merchant, category=cat,
                             channel=channel, on=on)
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO purchases (card_id, spent_on, amount, merchant, category,
                       channel, units, rate_units, rate_id, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    card.id, on.isoformat(), amount, merchant, cat, channel,
                    earning.units, earning.rate_units,
                    earning.rate.id if earning.rate else None, note,
                ),
            )
        return self.get_purchase(cur.lastrowid), earning

    def get_purchase(self, purchase_id: int) -> Purchase:
        row = self.conn.execute(_PURCHASE_SELECT + " WHERE p.id = ?", (purchase_id,)).fetchone()
        if row is None:
            raise ValueError(f"No purchase with id {purchase_id}")
        return db.purchase_from_row(row)

    def list_purchases(
        self,
        *,
        card: int | str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 50,
    ) -> list[Purchase]:
        """Newest first. `start` and `end` are inclusive."""
        clauses: list[str] = []
        params: list = []
        if card is not None:
            clauses.append("p.card_id = ?")
            params.append(self.get_card(card).id)
        if category:
            clauses.append("p.category = ?")
            params.append(parse_category(category).value)
        if merchant:
            clauses.append("p.merchant LIKE ? ESCAPE '\\'")
            params.append(_like(merchant.strip()))
        if start:
            clauses.append("p.spent_on >= ?")
            params.append(start.isoformat())
        if end:
            clauses.append("p.spent_on <= ?")
            params.append(end.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"{_PURCHASE_SELECT}{where} ORDER BY p.spent_on DESC, p.id DESC LIMIT ?",
            (*params, max(1, limit)),
        ).fetchall()
        return [db.purchase_from_row(r) for r in rows]

    def delete_purchase(self, purchase_id: int) -> Purchase:
        purchase = self.get_purchase(purchase_id)
        with self.conn:
            self.conn.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
        return purchase

    # ----------------------------------------------------------------- points

    def add_adjustment(
        self,
        card_ref: int | str,
        *,
        units: float,
        note: str,
        value_inr: float | None = None,
        made_on: date | None = None,
    ) -> dict:
        """Record points/cashback that did not come from a purchase.

        Positive units add (welcome bonus, milestone); negative units subtract
        (redemption, expiry).
        """
        card = self.get_card(card_ref)
        if units == 0:
            raise ValueError("units can't be zero")
        if not note.strip():
            raise ValueError("Add a short note, e.g. 'Redeemed for flight voucher'")
        on = made_on or date.today()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO adjustments (card_id, made_on, units, value_inr, note)"
                " VALUES (?, ?, ?, ?, ?)",
                (card.id, on.isoformat(), units, value_inr, note.strip()),
            )
        return {
            "id": cur.lastrowid,
            "card": card.name,
            "units": units,
            "value_inr": value_inr,
            "note": note.strip(),
            "date": on.isoformat(),
        }

    def adjustments_for(self, card_ref: int | str, limit: int = 20) -> list[dict]:
        card = self.get_card(card_ref)
        rows = self.conn.execute(
            "SELECT * FROM adjustments WHERE card_id = ? ORDER BY made_on DESC, id DESC LIMIT ?",
            (card.id, limit),
        ).fetchall()
        return [
            {"id": r["id"], "date": r["made_on"], "units": r["units"],
             "value_inr": r["value_inr"], "note": r["note"]}
            for r in rows
        ]

    def balances(self) -> list[Balance]:
        rows = self.conn.execute(
            """SELECT c.*,
                   COALESCE((SELECT SUM(units) FROM purchases p WHERE p.card_id = c.id), 0)
                       AS earned,
                   COALESCE((SELECT SUM(units) FROM adjustments a WHERE a.card_id = c.id), 0)
                       AS adjusted
               FROM cards c ORDER BY c.name"""
        ).fetchall()
        return [
            Balance(card=db.card_from_row(r), earned=round(r["earned"], 2),
                    adjusted=round(r["adjusted"], 2))
            for r in rows
        ]

    # ---------------------------------------------------------------- reports

    def spending_report(self, *, start: date | None = None, end: date | None = None) -> dict:
        """Totals by category and by card, with the reward return on each."""
        purchases = self.list_purchases(start=start, end=end, limit=1_000_000)

        def bucket() -> dict:
            return {"spent": 0.0, "rewards_inr": 0.0, "purchases": 0}

        by_category: dict[str, dict] = {}
        by_card: dict[str, dict] = {}
        for p in purchases:
            for key, table in ((p.category, by_category), (p.card_name, by_card)):
                row = table.setdefault(key, bucket())
                row["spent"] += p.amount
                row["rewards_inr"] += p.value_inr
                row["purchases"] += 1

        def finish(table: dict[str, dict]) -> list[dict]:
            out = []
            for name, row in sorted(table.items(), key=lambda kv: -kv[1]["spent"]):
                spent, value = round(row["spent"], 2), round(row["rewards_inr"], 2)
                out.append({
                    "name": name,
                    "spent": spent,
                    "rewards_inr": value,
                    "return_pct": round(value / spent * 100, 2) if spent else 0.0,
                    "purchases": row["purchases"],
                })
            return out

        total = round(sum(p.amount for p in purchases), 2)
        rewards = round(sum(p.value_inr for p in purchases), 2)
        return {
            "period": {
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
            },
            "total_spent": total,
            "total_rewards_inr": rewards,
            "overall_return_pct": round(rewards / total * 100, 2) if total else 0.0,
            "purchases": len(purchases),
            "by_category": finish(by_category),
            "by_card": finish(by_card),
        }
