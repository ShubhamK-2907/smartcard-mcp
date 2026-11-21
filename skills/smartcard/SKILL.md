---
name: smartcard
description: Pick the best credit card for any purchase and track card rewards with the SmartCard MCP server. Use this whenever the user asks which credit card to use, swipe or pay with, compares cards for a purchase (for example "₹5,000 on Amazon" or "booking a flight on MakeMyTrip"), logs a card spend, asks about reward points, cashback, monthly caps, statement cycles or redemptions, wants a spending or rewards report, or wants to add a credit card and its reward rules, even if they never mention SmartCard by name. Built for Indian credit cards with amounts in rupees.
license: PolyForm Strict 1.0.0. Copyright 2026 Shubham Kumar Sahoo. See LICENSE at https://github.com/ShubhamK-2907/smartcard-mcp
compatibility: Needs the SmartCard MCP server (https://github.com/ShubhamK-2907/smartcard-mcp), which runs locally with Python 3.12+ and uv.
---

# SmartCard

SmartCard is a local MCP server that knows the user's credit cards, their reward rules and how much of each monthly cap is already used. Your job with this skill is to turn everyday questions ("which card for this?", "I just paid for X") into the right SmartCard tool calls, and to explain the results in plain, short answers.

All amounts are Indian rupees. SmartCard never needs card numbers, CVVs or expiry dates, so never ask for them.

## 1. Check that SmartCard is connected

Look for SmartCard's tools (`list_cards`, `recommend_card`, `log_purchase` and so on; your client may show them with a prefix such as `mcp__smartcard__`). If they are missing, don't guess an answer from general knowledge, because the whole value is the user's own cards and caps. Tell the user it isn't connected and how to add it:

- Claude Code, as a plugin: `/plugin install smartcard --marketplace ShubhamK-2907/smartcard-mcp` (older versions: `/plugin marketplace add ShubhamK-2907/smartcard-mcp`, then `/plugin install smartcard@smartcard`)
- Any MCP client: run `uv --directory /path/to/smartcard-mcp run smartcard` as a stdio server (setup steps are in the repository README).

If the tools are there, start with `list_cards` when you don't yet know the user's wallet. An empty wallet means the next step is adding a card (section 4).

## 2. How SmartCard models rewards

Getting this right matters most when adding cards, because a wrong number silently skews every later recommendation.

- Every `block_inr` rupees spent earns `base_rate` units. Units are points for points cards, or rupees for cashback cards.
- `unit_value_inr` is what one point is worth in rupees (cashback is always 1).
- Points cards usually ignore partial blocks; cashback cards count them.
- An **earn rate** replaces the base rate (it is not added on top) for a category, a merchant name, or both, optionally only online or offline. When several match, the most specific wins: merchant beats category.
- A `monthly_cap` limits how many units an earn rate gives per statement cycle. Spend past the cap earns the base rate. Cycles start on the card's `statement_day`.
- Excluded categories earn nothing (by default fuel, rent, wallet loads and government payments).

Translating published terms:

| Card says | Set |
|---|---|
| 1% cashback on everything | `reward_kind=cashback, base_rate=1, block_inr=100` |
| 4 reward points per ₹150, 1 point = ₹0.50 | `reward_kind=points, base_rate=4, block_inr=150, unit_value_inr=0.5` |
| 5% cashback on Amazon, capped at ₹1,500/month | earn rate `merchant=Amazon, rate=5, monthly_cap=1500` |
| 10X points on dining (base 2 per ₹100), up to 2,000 points/month | earn rate `category=dining, rate=20, monthly_cap=2000` |

The last row is a common trap: "10X" on a base of 2 means 20 per block, and the earn rate holds the full 20, not the extra 18. If an issuer caps only the *bonus* part, convert that cap to total points at the accelerated rate before saving it.

## 3. Recommend a card for a purchase

Call `recommend_card` with the amount, the merchant and whether it's online or offline. Infer the channel when it's obvious (an app or website is online, a shop counter is offline) and only ask when it really changes the answer. Leave `category` empty for well-known merchants; SmartCard guesses it (Swiggy → dining, IRCTC → travel). For unknown merchants, pass a category yourself.

Answer briefly:

> Use **Demo Foodie**: 960 points, worth about **₹240 (10% back)**.
> Next best: Demo Cashback at ₹72 (3%).

Mention a cap when it affected the result ("your 5% shopping cap has ₹300 left this cycle, so part of this earns 1%"), and say so plainly if no card earns anything (for example on fuel).

## 4. Add a card the user owns

Never invent reward rates; a wrong rate quietly gives wrong advice later.

1. Ask for the card's exact name if it's ambiguous, then call `search_card_terms` (the `set_up_card` prompt walks through the same steps).
2. Summarise what you found in a short table: base earning, accelerated categories or merchants, caps, exclusions, point value. Point out anything uncertain or contradictory between sources.
3. Ask the user to confirm or correct it, including their statement date.
4. After they confirm, call `add_card` with `confirmed=true`, then `add_earn_rate` once per accelerated rule. Calling `add_card` without `confirmed` only returns a preview, which is a handy way to show the user exactly what will be saved.
5. Finish with `card_details` so they can check what was saved.

If the user gives the terms directly, skip the search but still restate them before saving.

## 5. Log purchases, balances and reports

- **Purchase made:** `log_purchase` with card, amount, merchant and channel (and the date if it wasn't today). Report what it earned. If the result includes `better_option`, mention it once, kindly, as a tip for next time.
- **Points balance:** `rewards_balance` (all cards, or one card).
- **Redemptions and bonuses:** `adjust_rewards` with negative units for redemptions or expiry and positive units for welcome or milestone bonuses, plus a short note.
- **How am I doing:** `spending_report` (defaults to the current month) and `list_purchases` for details. Lead with the overall return percentage, then the categories where the user earns the least.

## 6. Changes and deletions

Use `update_card` and `remove_earn_rate` for corrections. `delete_purchase` and `remove_card` delete data, so confirm with the user first. `remove_card` asks for this itself: call it once without `confirm` to see what would be deleted, show the user, and call again with `confirm=true` only after they agree.

## 7. When a tool returns an error

SmartCard's errors are written for you to act on, for example "No card matches 'gold'. Your cards: …" or "'gold' matches several cards". Use the list in the message to ask the user which card they meant instead of retrying blindly.

## Tone and limits

Keep answers short and concrete: card name, rupee value, percentage. SmartCard's numbers are estimates based on the terms entered; card issuers change their terms, so suggest re-checking a card's terms if the user relies on a large redemption. This is reward arithmetic, not financial advice.

For every tool's parameters, see [references/tools.md](references/tools.md).
