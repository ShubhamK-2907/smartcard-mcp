"""Spending categories SmartCard understands, plus helpers to map free text onto them."""

import re
from enum import StrEnum


class Category(StrEnum):
    DINING = "dining"
    GROCERIES = "groceries"
    SHOPPING = "shopping"
    TRAVEL = "travel"
    FUEL = "fuel"
    ENTERTAINMENT = "entertainment"
    UTILITIES = "utilities"
    TELECOM = "telecom"
    HEALTH = "health"
    EDUCATION = "education"
    INSURANCE = "insurance"
    RENT = "rent"
    WALLET = "wallet"
    GOVERNMENT = "government"
    OTHER = "other"


DESCRIPTIONS: dict[Category, str] = {
    Category.DINING: "Restaurants, cafes and food delivery",
    Category.GROCERIES: "Supermarkets and quick-commerce grocery apps",
    Category.SHOPPING: "Retail, apparel, electronics, online marketplaces",
    Category.TRAVEL: "Flights, hotels, trains, cabs and travel portals",
    Category.FUEL: "Petrol pumps and EV charging",
    Category.ENTERTAINMENT: "Movies, events and streaming subscriptions",
    Category.UTILITIES: "Electricity, water and gas bills",
    Category.TELECOM: "Mobile, broadband and DTH recharges",
    Category.HEALTH: "Pharmacies, hospitals and diagnostics",
    Category.EDUCATION: "School and college fees, courses",
    Category.INSURANCE: "Insurance premiums",
    Category.RENT: "Rent paid by card",
    Category.WALLET: "Loading prepaid wallets",
    Category.GOVERNMENT: "Taxes and government payments",
    Category.OTHER: "Anything that fits nowhere else",
}

# Categories that most Indian card programmes do not reward.
# New cards start with these excluded unless told otherwise.
DEFAULT_EXCLUSIONS: frozenset[Category] = frozenset(
    {Category.FUEL, Category.RENT, Category.WALLET, Category.GOVERNMENT}
)

_SYNONYMS: dict[str, Category] = {
    "food": Category.DINING,
    "restaurant": Category.DINING,
    "restaurants": Category.DINING,
    "food delivery": Category.DINING,
    "grocery": Category.GROCERIES,
    "online shopping": Category.SHOPPING,
    "retail": Category.SHOPPING,
    "flight": Category.TRAVEL,
    "flights": Category.TRAVEL,
    "hotel": Category.TRAVEL,
    "hotels": Category.TRAVEL,
    "cab": Category.TRAVEL,
    "train": Category.TRAVEL,
    "petrol": Category.FUEL,
    "diesel": Category.FUEL,
    "movies": Category.ENTERTAINMENT,
    "streaming": Category.ENTERTAINMENT,
    "bills": Category.UTILITIES,
    "electricity": Category.UTILITIES,
    "mobile": Category.TELECOM,
    "recharge": Category.TELECOM,
    "broadband": Category.TELECOM,
    "medical": Category.HEALTH,
    "pharmacy": Category.HEALTH,
    "fees": Category.EDUCATION,
    "tax": Category.GOVERNMENT,
    "taxes": Category.GOVERNMENT,
}

# Well-known merchants, matched as whole words in the merchant text.
_MERCHANTS: dict[Category, tuple[str, ...]] = {
    Category.DINING: ("swiggy", "zomato", "eatsure", "starbucks", "dominos", "mcdonalds"),
    Category.GROCERIES: ("bigbasket", "blinkit", "zepto", "dmart", "instamart", "jiomart"),
    Category.SHOPPING: ("amazon", "flipkart", "myntra", "ajio", "nykaa", "croma", "meesho"),
    Category.TRAVEL: (
        "makemytrip", "goibibo", "cleartrip", "irctc", "indigo", "air india",
        "uber", "ola", "rapido", "agoda", "booking.com",
    ),
    Category.FUEL: ("indian oil", "iocl", "hpcl", "bharat petroleum", "bpcl", "shell"),
    Category.ENTERTAINMENT: ("bookmyshow", "pvr", "inox", "netflix", "hotstar", "spotify"),
    Category.TELECOM: ("jio", "airtel", "vodafone", "vi recharge", "bsnl"),
    Category.HEALTH: ("apollo", "pharmeasy", "1mg", "netmeds", "practo"),
    Category.UTILITIES: ("bescom", "tata power", "adani electricity", "mahanagar gas"),
}


def parse_category(value: str) -> Category:
    """Turn user or LLM text ("Food", "online shopping") into a Category."""
    key = value.strip().lower()
    try:
        return Category(key)
    except ValueError:
        pass
    if key in _SYNONYMS:
        return _SYNONYMS[key]
    valid = ", ".join(c.value for c in Category)
    raise ValueError(f"Unknown category '{value}'. Use one of: {valid}")


def guess_category(merchant: str) -> Category | None:
    """Best-effort category for a merchant name, or None if it is not a known brand."""
    text = merchant.lower()
    for category, names in _MERCHANTS.items():
        for name in names:
            if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text):
                return category
    return None
