# Copyright (c) 2026 Shubham Kumar Sahoo. Licensed under the PolyForm Strict License 1.0.0 (see LICENSE).
"""SQLite storage: connection, schema and row mapping."""

import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from smartcard.models import Card, EarnRate, Purchase

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    issuer         TEXT    NOT NULL,
    network        TEXT,
    reward_kind    TEXT    NOT NULL CHECK (reward_kind IN ('points', 'cashback')),
    base_rate      REAL    NOT NULL CHECK (base_rate >= 0),
    block_inr      REAL    NOT NULL CHECK (block_inr > 0),
    whole_blocks   INTEGER NOT NULL,
    unit_value_inr REAL    NOT NULL CHECK (unit_value_inr >= 0),
    statement_day  INTEGER NOT NULL CHECK (statement_day BETWEEN 1 AND 28),
    excluded       TEXT    NOT NULL DEFAULT '[]',
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS earn_rates (
    id          INTEGER PRIMARY KEY,
    card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    rate        REAL    NOT NULL CHECK (rate >= 0),
    category    TEXT,
    merchant    TEXT,
    channel     TEXT    NOT NULL DEFAULT 'any' CHECK (channel IN ('any', 'online', 'offline')),
    monthly_cap REAL    CHECK (monthly_cap IS NULL OR monthly_cap > 0),
    label       TEXT,
    CHECK (category IS NOT NULL OR merchant IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY,
    card_id    INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    spent_on   TEXT    NOT NULL,
    amount     REAL    NOT NULL CHECK (amount > 0),
    merchant   TEXT    NOT NULL,
    category   TEXT    NOT NULL,
    channel    TEXT    NOT NULL,
    units      REAL    NOT NULL,
    rate_units REAL    NOT NULL DEFAULT 0,
    rate_id    INTEGER REFERENCES earn_rates(id) ON DELETE SET NULL,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS purchases_by_card_date ON purchases(card_id, spent_on);

CREATE TABLE IF NOT EXISTS adjustments (
    id        INTEGER PRIMARY KEY,
    card_id   INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    made_on   TEXT    NOT NULL,
    units     REAL    NOT NULL,
    value_inr REAL,
    note      TEXT    NOT NULL
);
"""


def default_db_path() -> Path:
    """Database location: $SMARTCARD_DB, or ~/.smartcard/smartcard.db."""
    return Path(os.environ.get("SMARTCARD_DB", "~/.smartcard/smartcard.db")).expanduser()


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def card_from_row(row: sqlite3.Row) -> Card:
    return Card(
        id=row["id"],
        name=row["name"],
        issuer=row["issuer"],
        network=row["network"],
        reward_kind=row["reward_kind"],
        base_rate=row["base_rate"],
        block_inr=row["block_inr"],
        whole_blocks=bool(row["whole_blocks"]),
        unit_value_inr=row["unit_value_inr"],
        statement_day=row["statement_day"],
        excluded=frozenset(json.loads(row["excluded"])),
        notes=row["notes"],
    )


def rate_from_row(row: sqlite3.Row) -> EarnRate:
    return EarnRate(
        id=row["id"],
        card_id=row["card_id"],
        rate=row["rate"],
        category=row["category"],
        merchant=row["merchant"],
        channel=row["channel"],
        monthly_cap=row["monthly_cap"],
        label=row["label"],
    )


def purchase_from_row(row: sqlite3.Row) -> Purchase:
    """Expects the purchase columns plus `card_name` and `unit_value_inr` from cards."""
    return Purchase(
        id=row["id"],
        card_id=row["card_id"],
        card_name=row["card_name"],
        spent_on=date.fromisoformat(row["spent_on"]),
        amount=row["amount"],
        merchant=row["merchant"],
        category=row["category"],
        channel=row["channel"],
        units=row["units"],
        value_inr=round(row["units"] * row["unit_value_inr"], 2),
        rate_id=row["rate_id"],
        note=row["note"],
    )
