from datetime import date

import pytest

from smartcard.demo import load_demo


def add_cashback(wallet, name="Cash One", **kw):
    return wallet.add_card(name=name, issuer="Bank A", reward_kind="cashback",
                           base_rate=1, statement_day=10, **kw)


def add_points(wallet, name="Points One"):
    return wallet.add_card(name=name, issuer="Bank B", reward_kind="points", base_rate=4,
                           block_inr=150, unit_value_inr=0.5, statement_day=1)


class TestCards:
    def test_add_and_find_by_id_name_and_fragment(self, wallet):
        card = add_cashback(wallet)
        assert wallet.get_card(card.id) == card
        assert wallet.get_card(str(card.id)) == card
        assert wallet.get_card("cash one") == card
        assert wallet.get_card("one") == card

    def test_defaults(self, wallet):
        card = add_cashback(wallet)
        assert card.unit_value_inr == 1
        assert card.whole_blocks is False
        assert {"fuel", "rent", "wallet", "government"} == card.excluded
        assert add_points(wallet).whole_blocks is True

    def test_points_card_needs_point_value(self, wallet):
        with pytest.raises(ValueError, match="unit_value_inr"):
            wallet.add_card(name="P", issuer="B", reward_kind="points", base_rate=1)

    def test_duplicate_name_rejected(self, wallet):
        add_cashback(wallet)
        with pytest.raises(ValueError, match="already exists"):
            add_cashback(wallet, name="CASH ONE")

    def test_ambiguous_and_missing_names(self, wallet):
        add_cashback(wallet, name="Gold Card")
        add_cashback(wallet, name="Gold Plus")
        with pytest.raises(ValueError, match="several cards"):
            wallet.get_card("gold")
        with pytest.raises(ValueError, match="No card matches"):
            wallet.get_card("silver")

    def test_update(self, wallet):
        card = add_cashback(wallet)
        updated = wallet.update_card(card.id, statement_day=20, excluded_categories=["rent"])
        assert updated.statement_day == 20
        assert updated.excluded == {"rent"}
        with pytest.raises(ValueError):
            wallet.update_card(card.id, statement_day=31)

    def test_remove_cascades(self, wallet):
        card = add_cashback(wallet)
        wallet.add_rate(card.id, rate=5, category="dining")
        wallet.log_purchase(card.id, amount=100, merchant="Swiggy")
        wallet.remove_card(card.id)
        assert wallet.list_cards() == []
        assert wallet.list_purchases() == []
        assert wallet.conn.execute("SELECT COUNT(*) FROM earn_rates").fetchone()[0] == 0


class TestPurchases:
    def test_category_guessed_from_merchant(self, wallet):
        card = add_cashback(wallet)
        purchase, _ = wallet.log_purchase(card.id, amount=500, merchant="Zomato order")
        assert purchase.category == "dining"

    def test_unknown_merchant_needs_category(self, wallet):
        card = add_cashback(wallet)
        with pytest.raises(ValueError, match="category"):
            wallet.log_purchase(card.id, amount=500, merchant="Corner Shop")

    def test_cap_is_shared_across_purchases_and_resets_next_cycle(self, wallet):
        card = add_cashback(wallet)  # statement day 10
        wallet.add_rate(card.id, rate=5, category="shopping", monthly_cap=100)
        day = date(2026, 5, 12)
        _, e1 = wallet.log_purchase(card.id, amount=1600, merchant="Amazon", spent_on=day)
        assert e1.units == 80
        _, e2 = wallet.log_purchase(card.id, amount=1000, merchant="Amazon", spent_on=day)
        # 20 left at 5% covers ₹400; the other ₹600 earns 1%.
        assert e2.units == 26
        _, e3 = wallet.log_purchase(card.id, amount=1000, merchant="Amazon",
                                    spent_on=date(2026, 6, 10))
        assert e3.units == 50

    def test_filters(self, wallet):
        a = add_cashback(wallet)
        b = add_points(wallet)
        wallet.log_purchase(a.id, amount=100, merchant="Swiggy", spent_on=date(2026, 1, 5))
        wallet.log_purchase(b.id, amount=300, merchant="Amazon", spent_on=date(2026, 2, 5))
        assert [p.merchant for p in wallet.list_purchases(card="Points")] == ["Amazon"]
        assert [p.merchant for p in wallet.list_purchases(category="food")] == ["Swiggy"]
        assert len(wallet.list_purchases(start=date(2026, 2, 1))) == 1
        assert len(wallet.list_purchases(end=date(2026, 1, 5))) == 1

    def test_delete(self, wallet):
        card = add_cashback(wallet)
        p, _ = wallet.log_purchase(card.id, amount=100, merchant="Swiggy")
        wallet.delete_purchase(p.id)
        with pytest.raises(ValueError):
            wallet.get_purchase(p.id)


class TestRecommend:
    def test_ranks_by_rupee_value(self, wallet):
        cash = add_cashback(wallet)
        points = add_points(wallet)
        wallet.add_rate(points.id, rate=30, category="travel")  # 10% back
        wallet.add_rate(cash.id, rate=5, category="travel")
        cat, ranking = wallet.recommend(amount=3000, merchant="IndiGo")
        assert cat == "travel"
        assert [c.name for c, _ in ranking] == ["Points One", "Cash One"]
        assert ranking[0][1].value_inr == 300

    def test_empty_wallet(self, wallet):
        with pytest.raises(ValueError, match="empty"):
            wallet.recommend(amount=100, merchant="Amazon")


class TestBalancesAndReports:
    def test_balance_includes_adjustments(self, wallet):
        card = add_points(wallet)
        wallet.log_purchase(card.id, amount=1500, merchant="Amazon")  # 40 points
        wallet.add_adjustment(card.id, units=1000, note="Welcome bonus")
        wallet.add_adjustment(card.id, units=-500, note="Redeemed", value_inr=250)
        (balance,) = wallet.balances()
        assert balance.earned == 40
        assert balance.adjusted == 500
        assert balance.balance == 540
        assert balance.value_inr == 270

    def test_spending_report(self, wallet):
        card = add_cashback(wallet)
        wallet.log_purchase(card.id, amount=1000, merchant="Swiggy", spent_on=date(2026, 3, 1))
        wallet.log_purchase(card.id, amount=3000, merchant="Indian Oil", spent_on=date(2026, 3, 2))
        report = wallet.spending_report(start=date(2026, 3, 1), end=date(2026, 3, 31))
        assert report["total_spent"] == 4000
        assert report["total_rewards_inr"] == 10
        assert report["overall_return_pct"] == 0.25
        assert [c["name"] for c in report["by_category"]] == ["fuel", "dining"]

    def test_demo_data_loads(self, wallet):
        load_demo(wallet, today=date(2026, 11, 21))
        assert len(wallet.list_cards()) == 3
        assert len(wallet.list_purchases()) == 10
