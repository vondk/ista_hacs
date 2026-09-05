"""Constants for the ista Online (DK) integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "ista_online"

# Config / options keys
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_CONS_ID: Final = "cons_id"
CONF_METER_TYPE: Final = "meter_type"
CONF_PRICE: Final = "price"
CONF_PRICES: Final = "prices"
# User-supplied per-meter display names: {meter_id: name}
CONF_METER_NAMES: Final = "meter_names"
# CSV export period selection (Telerik RadComboBoxFrom/ToYear values). An empty
# string means "use whatever the popup page defaults to".
CONF_FROM_PERIOD: Final = "from_period"
CONF_TO_PERIOD: Final = "to_period"

# Keys used inside a single price period dict stored under CONF_PRICES
CONF_PERIOD_START: Final = "start"
CONF_PERIOD_END: Final = "end"

DEFAULT_METER_TYPE: Final = "HCA"

# istaonline.dk endpoints
BASE_URL: Final = "https://www.istaonline.dk"
LOGIN_URL: Final = f"{BASE_URL}/Tenant.aspx"
POPUP_URL: Final = f"{BASE_URL}/PopUp.aspx"

BROWSER_HEADERS: Final = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}

# Persistent storage of accumulated daily readings (like history.csv)
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}_readings"

# Statistic id building blocks (external statistics require "domain:object_id")
STAT_TOTAL_ENERGY: Final = f"{DOMAIN}:total_energy"
STAT_TOTAL_COST: Final = f"{DOMAIN}:total_cost"

# Unit used for the consumption statistic. Home Assistant has no "heat" unit, so
# HCA units are surfaced as gas volume (m³) to appear in the Energy dashboard.
STAT_ENERGY_UNIT: Final = "m³"
STAT_COST_UNIT: Final = "DKK"
