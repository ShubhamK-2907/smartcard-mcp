"""End-to-end tests through a real MCP client session (in memory, no subprocess)."""

import json

import pytest
from mcp import Client

from smartcard import server
from smartcard.wallet import Wallet

pytestmark = pytest.mark.anyio


@pytest.fixture
def fresh_wallet():
    server.use_wallet(Wallet.open(":memory:"))
    yield
    server.use_wallet(None)


async def call(session, tool, **args) -> dict:
    result = await session.call_tool(tool, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_lists_tools_resource_and_prompt(fresh_wallet):
    async with Client(server.mcp) as session:
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"recommend_card", "log_purchase", "add_card", "spending_report"} <= tools
        resources = [str(r.uri) for r in (await session.list_resources()).resources]
        assert resources == ["smartcard://categories"]
        prompts = [p.name for p in (await session.list_prompts()).prompts]
        assert prompts == ["set_up_card"]


async def test_add_card_recommend_and_log(fresh_wallet):
    async with Client(server.mcp) as session:
        await call(session, "add_card", name="Daily Cash", issuer="Bank",
                   reward_kind="cashback", base_rate=1)
        await call(session, "add_card", name="Food Points", issuer="Bank",
                   reward_kind="points", base_rate=2, unit_value_inr=0.25)
        await call(session, "add_earn_rate", card="Food Points", rate=40, merchant="Swiggy")

        rec = await call(session, "recommend_card", amount=1000, merchant="Swiggy",
                         channel="online")
        assert rec["purchase"]["category"] == "dining"
        assert rec["best"]["card"] == "Food Points"
        assert rec["best"]["value_inr"] == 100

        logged = await call(session, "log_purchase", card="Daily Cash", amount=1000,
                            merchant="Swiggy", channel="online", spent_on="2026-11-01")
        assert logged["logged"]["rewards_inr"] == 10
        assert logged["better_option"]["card"] == "Food Points"
        assert logged["better_option"]["missed_inr"] == 90


async def test_remove_card_needs_confirmation(fresh_wallet):
    async with Client(server.mcp) as session:
        await call(session, "add_card", name="Old Card", issuer="Bank",
                   reward_kind="cashback", base_rate=1)
        preview = await call(session, "remove_card", card="Old Card")
        assert preview["deleted"] is False
        assert (await call(session, "list_cards"))["count"] == 1
        done = await call(session, "remove_card", card="Old Card", confirm=True)
        assert done["deleted"] is True
        assert (await call(session, "list_cards"))["count"] == 0


async def test_errors_are_reported_to_the_client(fresh_wallet):
    async with Client(server.mcp) as session:
        result = await session.call_tool("card_details", {"card": "nope"})
        assert result.is_error
        assert "No card matches" in result.content[0].text


async def test_categories_resource(fresh_wallet):
    async with Client(server.mcp) as session:
        res = await session.read_resource("smartcard://categories")
        data = json.loads(res.contents[0].text)
        assert "dining" in data["categories"]
        assert "fuel" in data["excluded_by_default"]
