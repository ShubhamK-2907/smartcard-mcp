# Copyright (c) 2026 Shubham Kumar Sahoo. Licensed under the PolyForm Strict License 1.0.0 (see LICENSE).
"""Load a small fictional wallet so SmartCard can be tried without real card data.

Usage:  smartcard-demo            (uses $SMARTCARD_DB or ~/.smartcard/smartcard.db)
        smartcard-demo --db PATH
"""

import argparse
from datetime import date, timedelta

from smartcard.db import default_db_path
from smartcard.wallet import Wallet


def load_demo(wallet: Wallet, today: date | None = None) -> None:
    today = today or date.today()

    cashback = wallet.add_card(
        name="Demo Cashback", issuer="Demo Bank", network="Visa", reward_kind="cashback",
        base_rate=1, statement_day=10, notes="Fictional card for trying SmartCard",
    )
    wallet.add_rate(cashback.id, rate=5, category="shopping", channel="online",
                    monthly_cap=1500, label="5% online shopping")
    wallet.add_rate(cashback.id, rate=3, category="dining", label="3% dining")

    voyager = wallet.add_card(
        name="Demo Voyager", issuer="Demo Bank", network="Mastercard", reward_kind="points",
        base_rate=4, block_inr=150, unit_value_inr=0.5, statement_day=1,
        notes="Fictional travel card: 4 points per ₹150, 1 point = ₹0.50",
    )
    wallet.add_rate(voyager.id, rate=20, category="travel", monthly_cap=4000,
                    label="5x on travel")

    foodie = wallet.add_card(
        name="Demo Foodie", issuer="Sample Bank", network="RuPay", reward_kind="points",
        base_rate=2, unit_value_inr=0.25, statement_day=20,
        notes="Fictional dining card: 2 points per ₹100, 1 point = ₹0.25",
    )
    wallet.add_rate(foodie.id, rate=40, merchant="Swiggy", monthly_cap=2000,
                    label="10% back on Swiggy")
    wallet.add_rate(foodie.id, rate=20, category="dining", label="5% on dining")

    purchases = [
        (cashback, 1250, "Amazon", "online", 18),
        (cashback, 3400, "Myntra", "online", 12),
        (cashback, 900, "Starbucks", "offline", 9),
        (voyager, 7800, "IndiGo", "online", 15),
        (voyager, 2200, "Croma", "offline", 7),
        (voyager, 640, "Uber", "online", 3),
        (foodie, 560, "Swiggy", "online", 11),
        (foodie, 2400, "Barbeque Nation", "offline", 5),
        (foodie, 1850, "BigBasket", "online", 2),
        (cashback, 2000, "Indian Oil", "offline", 1),
    ]
    category_for = {"Barbeque Nation": "dining"}
    for card, amount, merchant, channel, days_ago in purchases:
        wallet.log_purchase(
            card.id, amount=amount, merchant=merchant, channel=channel,
            category=category_for.get(merchant), spent_on=today - timedelta(days=days_ago),
        )
    wallet.add_adjustment(voyager.id, units=2000, note="Welcome bonus",
                          made_on=today - timedelta(days=20))


def main() -> None:
    parser = argparse.ArgumentParser(description="Load fictional demo cards into SmartCard")
    parser.add_argument("--db", default=None, help="Database path")
    args = parser.parse_args()

    path = args.db or default_db_path()
    wallet = Wallet.open(path)
    if wallet.list_cards():
        raise SystemExit(f"{path} already has cards; demo data not loaded.")
    load_demo(wallet)
    print(f"Loaded {len(wallet.list_cards())} demo cards and "
          f"{len(wallet.list_purchases(limit=1000))} purchases into {path}")


if __name__ == "__main__":
    main()
