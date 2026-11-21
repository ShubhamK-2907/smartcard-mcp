# SmartCard tool reference

Every tool takes a card as `card`: its id, its full name, or a unique part of its name. Dates are `YYYY-MM-DD`. Amounts are rupees.

## Cards

| Tool | Parameters | Notes |
|---|---|---|
| `list_cards` | none | Cards with base earning and return % |
| `card_details` | `card` | Earn rates (with ids), cap usage this statement cycle, balance, recent adjustments |
| `add_card` | `name`, `issuer`, `reward_kind` (`points` or `cashback`), `base_rate`; optional `block_inr` (default 100), `unit_value_inr` (required for points cards), `statement_day` (1–28, default 1), `network`, `excluded_categories` (default fuel, rent, wallet, government), `notes`, `confirmed` (default false) | Without `confirmed=true` it only previews; save after the user agrees, then `add_earn_rate` for each accelerated rule |
| `update_card` | `card` plus any of `name`, `issuer`, `base_rate`, `block_inr`, `unit_value_inr`, `statement_day`, `network`, `excluded_categories` (replaces the list), `notes` | Only passed fields change |
| `remove_card` | `card`, `confirm` (default false) | Without `confirm` it only previews what would be deleted |
| `add_earn_rate` | `card`, `rate`; at least one of `category` or `merchant`; optional `channel` (`any`, `online`, `offline`), `monthly_cap`, `label` | `rate` replaces the base rate and is in the card's units per `block_inr` |
| `remove_earn_rate` | `rate_id` | Ids come from `card_details` |

## Purchases

| Tool | Parameters | Notes |
|---|---|---|
| `recommend_card` | `amount`, `merchant`; optional `category`, `channel` (`online` or `offline`, default offline) | Ranks every card by rupee value; respects caps already used |
| `log_purchase` | `card`, `amount`, `merchant`; optional `category`, `channel`, `spent_on`, `note` | Returns rewards earned and `better_option` when another card would have earned more |
| `list_purchases` | optional `card`, `category`, `merchant` (partial match), `start`, `end` (inclusive), `limit` (default 20) | Newest first |
| `delete_purchase` | `purchase_id` | Confirm with the user first |

## Rewards and insight

| Tool | Parameters | Notes |
|---|---|---|
| `rewards_balance` | optional `card` | Earned + adjustments = balance, with rupee value |
| `adjust_rewards` | `card`, `units` (negative = redemption or expiry), `note`; optional `value_inr`, `on` | For points that didn't come from a purchase |
| `spending_report` | optional `start`, `end` | Defaults to the current calendar month; totals by category and card |
| `search_card_terms` | `card_name`; optional `topic`, `max_results` (1–15) | Web search snippets; confirm with the user before saving anything |

## Resource and prompt

- Resource `smartcard://categories`: the 15 category names with descriptions and the default exclusions.
- Prompt `set_up_card(card_name)`: a guided flow for adding a card with correct rates.

## Categories

dining, groceries, shopping, travel, fuel, entertainment, utilities, telecom, health, education, insurance, rent, wallet, government, other. Common synonyms such as "food", "flights" or "petrol" are accepted.
