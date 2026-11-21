from datetime import date

import pytest

from smartcard.models import Card, EarnRate
from smartcard.rewards import compute_earning, pick_rate, spend_blocks, statement_cycle


def make_card(**overrides) -> Card:
    fields = dict(
        id=1, name="Test Points", issuer="Bank", reward_kind="points", base_rate=4,
        block_inr=150, whole_blocks=True, unit_value_inr=0.5, statement_day=15,
        excluded=frozenset({"fuel"}),
    )
    fields.update(overrides)
    return Card(**fields)


def earn(card, rates=(), amount=1000.0, merchant="Shop", category="shopping",
         channel="offline", cap_used=None):
    return compute_earning(card, list(rates), amount=amount, merchant=merchant,
                           category=category, channel=channel, cap_used=cap_used or {})


class TestStatementCycle:
    def test_on_or_after_statement_day(self):
        assert statement_cycle(15, date(2026, 3, 20)) == (date(2026, 3, 15), date(2026, 4, 15))
        assert statement_cycle(15, date(2026, 3, 15)) == (date(2026, 3, 15), date(2026, 4, 15))

    def test_before_statement_day_belongs_to_previous_cycle(self):
        assert statement_cycle(15, date(2026, 3, 14)) == (date(2026, 2, 15), date(2026, 3, 15))

    def test_year_boundaries(self):
        assert statement_cycle(10, date(2026, 1, 5)) == (date(2025, 12, 10), date(2026, 1, 10))
        assert statement_cycle(10, date(2026, 12, 25)) == (date(2026, 12, 10), date(2027, 1, 10))


class TestBlocks:
    def test_points_cards_ignore_partial_blocks(self):
        assert spend_blocks(make_card(), 299) == 1
        assert spend_blocks(make_card(), 300) == 2

    def test_cashback_cards_count_partial_blocks(self):
        card = make_card(reward_kind="cashback", block_inr=100, whole_blocks=False)
        assert spend_blocks(card, 250) == 2.5


class TestPickRate:
    def test_merchant_beats_category(self):
        cat = EarnRate(id=1, card_id=1, rate=10, category="dining")
        merch = EarnRate(id=2, card_id=1, rate=5, merchant="swiggy")
        assert pick_rate([cat, merch], "Swiggy Instamart", "dining", "online") == merch

    def test_higher_rate_wins_a_tie(self):
        low = EarnRate(id=1, card_id=1, rate=2, category="dining")
        high = EarnRate(id=2, card_id=1, rate=6, category="dining")
        assert pick_rate([low, high], "Cafe", "dining", "offline") == high

    def test_channel_restriction(self):
        online = EarnRate(id=1, card_id=1, rate=5, category="shopping", channel="online")
        assert pick_rate([online], "Croma", "shopping", "offline") is None
        assert pick_rate([online], "Croma", "shopping", "online") == online


class TestComputeEarning:
    def test_base_rate(self):
        e = earn(make_card(), amount=1000)  # 6 whole blocks of ₹150
        assert e.units == 24
        assert e.value_inr == 12
        assert e.rate is None

    def test_excluded_category_earns_nothing(self):
        e = earn(make_card(), category="fuel")
        assert e.units == 0 and e.value_inr == 0

    def test_accelerated_rate_replaces_base(self):
        rate = EarnRate(id=7, card_id=1, rate=20, category="travel")
        e = earn(make_card(), [rate], amount=1500, category="travel")
        assert e.units == 200
        assert e.rate == rate

    def test_cap_overflow_falls_back_to_base_rate(self):
        rate = EarnRate(id=7, card_id=1, rate=20, category="travel", monthly_cap=500)
        # 15 blocks would earn 300 at the rate, but only 100 of the cap is left.
        e = earn(make_card(), [rate], amount=2250, category="travel", cap_used={7: 400})
        # 100 units use 5 blocks at the rate; the other 10 blocks earn 4 each.
        assert e.rate_units == 100
        assert e.units == 140

    def test_cap_already_used_up(self):
        rate = EarnRate(id=7, card_id=1, rate=20, category="travel", monthly_cap=500)
        e = earn(make_card(), [rate], amount=1500, category="travel", cap_used={7: 500})
        assert e.rate_units == 0
        assert e.units == 40

    def test_cashback_value(self):
        card = make_card(reward_kind="cashback", base_rate=1, block_inr=100,
                         whole_blocks=False, unit_value_inr=1)
        e = earn(card, amount=1234)
        assert e.value_inr == 12.34
        assert e.return_pct == 1.0

    def test_rejects_non_positive_amount(self):
        with pytest.raises(ValueError):
            earn(make_card(), amount=0)
