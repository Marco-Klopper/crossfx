"""
USD/ZAR exchange rate lookup. Swap the implementation based on
settings.exchange_rate_source (mock | api | table) without changing callers.
"""
from decimal import Decimal

from app.config import settings


def get_usd_zar_rate() -> Decimal:
    if settings.exchange_rate_source == "mock":
        return Decimal("18.50")  # TODO: replace with a believable, occasionally-varying mock
    elif settings.exchange_rate_source == "api":
        # TODO: call a public FX API (e.g. exchangerate.host, Open Exchange Rates) with caching
        raise NotImplementedError
    elif settings.exchange_rate_source == "table":
        # TODO: read from a manually configured rate table in the DB
        raise NotImplementedError
    raise ValueError(f"Unknown exchange_rate_source: {settings.exchange_rate_source}")
