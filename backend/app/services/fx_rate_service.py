"""
USD/ZAR exchange rate lookup (spec §5).

One function, three interchangeable sources, chosen by
settings.exchange_rate_source (mock | api | table) — callers never change:

  mock   a deterministic rate that drifts slowly around a configured base.
         No network, so quoting stays pure compute, which is what
         performance-testing/README.md benchmarks. Deterministic *within*
         a cache bucket, so the rate a sender is quoted does not move
         between rendering the quote screen and posting it.
  api    a public FX endpoint, fetched at most once per
         fx_rate_cache_seconds and reused in between. If a refresh fails
         the last good rate is served rather than failing the quote.
  table  the most recent fx_rates row — a rate a human pinned, for a
         scripted demo or an offline marking session.

The rate returned here is the *mid-market* rate. CrossFX's spread is not
hidden in it: it is charged once, visibly, as fee_service's fx_margin_zar
line (spec §4).
"""
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Matches remittances.fx_rate_used, Numeric(12, 6).
RATE_QUANTUM = Decimal("0.000001")


class FxRateUnavailableError(Exception):
    """
    No rate could be produced. Callers map this to a 503: a quote with a
    guessed rate would be worse than no quote.
    """


def get_usd_zar_rate(db: Session | None = None) -> Decimal:
    """
    The current USD/ZAR mid-market rate, as ZAR per 1 USD.

    `db` is only consulted by the `table` source; the other two ignore it,
    so callers can pass their request session unconditionally.
    """
    source = settings.exchange_rate_source
    if source == "mock":
        return _mock_rate()
    if source == "api":
        return _api_rate()
    if source == "table":
        return _table_rate(db)
    raise ValueError(f"Unknown exchange_rate_source: {source}")


def _quantise(rate: Decimal) -> Decimal:
    if rate <= 0:
        raise FxRateUnavailableError(f"Non-positive USD/ZAR rate: {rate}")
    return rate.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


# -- mock -------------------------------------------------------------------


def _mock_rate(now: float | None = None) -> Decimal:
    """
    A believable rate rather than a constant: it moves, but only once per
    cache bucket, and it is a pure function of the clock.

    Deriving the offset from a hash of the bucket number — rather than
    `random` — means two API workers quoting the same second agree, a
    reloaded process agrees with itself, and a test can pin a rate by
    pinning the clock. Setting FX_MOCK_VOLATILITY_BPS=0 flattens it
    entirely, which is what a scripted demo wants.
    """
    base = Decimal(str(settings.fx_mock_base_rate))
    volatility = Decimal(settings.fx_mock_volatility_bps) / Decimal(10_000)
    if volatility == 0:
        return _quantise(base)

    bucket = int(now if now is not None else time.time()) // max(
        settings.fx_rate_cache_seconds, 1
    )
    digest = hashlib.sha256(str(bucket).encode()).digest()
    # First four bytes as a fraction of the way through [0, 1), mapped to
    # [-1, 1] so the walk is symmetric around the base rate.
    fraction = Decimal(int.from_bytes(digest[:4], "big")) / Decimal(2**32)
    offset = (fraction * 2 - 1) * volatility
    return _quantise(base * (1 + offset))


# -- api --------------------------------------------------------------------

# Module-level cache guarded by a lock: several request threads share one
# process, and without the lock a burst of quotes would each fire their own
# HTTP request while the cache is cold.
# (expires_at, rate, fetched_at). fetched_at is what bounds how stale a
# fallback may get; without it the cache could not tell "refreshed a
# minute ago" from "last succeeded on Tuesday".
_api_cache: dict[str, tuple[float, Decimal, float]] = {}
_api_lock = threading.Lock()


def _api_rate() -> Decimal:
    cached = _api_cache.get(settings.fx_api_url)
    if cached is not None and time.time() < cached[0]:
        return cached[1]

    with _api_lock:
        # Re-check: another thread may have refreshed while we waited.
        cached = _api_cache.get(settings.fx_api_url)
        if cached is not None and time.time() < cached[0]:
            return cached[1]

        try:
            rate = _quantise(_fetch_rate())
        except Exception as exc:
            if cached is not None:
                return _fallback(cached, exc)
            raise FxRateUnavailableError(
                f"Could not fetch USD/ZAR from {settings.fx_api_url}: {exc}"
            ) from exc

        now = time.time()
        _api_cache[settings.fx_api_url] = (
            now + settings.fx_rate_cache_seconds,
            rate,
            now,
        )
        return rate


def _fallback(cached: tuple[float, Decimal, float], exc: Exception) -> Decimal:
    """
    The last good rate, if it is still recent enough to price with.

    Stale but real beats failing the quote — refusing to quote whenever
    the provider blips would make the whole send flow depend on a third
    party's uptime. Past fx_rate_max_stale_seconds that argument stops
    holding: a rate old enough to be wrong is worse than no rate, because
    the sender is held to it.
    """
    _expires_at, rate, fetched_at = cached
    age = time.time() - fetched_at
    if age > settings.fx_rate_max_stale_seconds:
        raise FxRateUnavailableError(
            f"Could not fetch USD/ZAR from {settings.fx_api_url} ({exc}), and "
            f"the last good rate is {int(age)}s old — older than the "
            f"{settings.fx_rate_max_stale_seconds}s this corridor will price "
            f"against"
        ) from exc

    logger.warning(
        "FX refresh failed (%s), serving the last good rate %s (%ss old)",
        exc,
        rate,
        int(age),
    )
    return rate


def _fetch_rate() -> Decimal:
    response = httpx.get(
        settings.fx_api_url, timeout=settings.fx_api_timeout_seconds
    )
    response.raise_for_status()
    return _extract_rate(response.json(), settings.fx_api_rate_path)


def _extract_rate(payload: object, path: str) -> Decimal:
    """
    Walks a dotted path into the response, so changing provider is a
    FX_API_RATE_PATH change rather than a code change.
    """
    cursor = payload
    for key in path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            raise FxRateUnavailableError(
                f"FX response has no '{path}' (stopped at '{key}')"
            )
        cursor = cursor[key]
    if not isinstance(cursor, (int, float, str)):
        raise FxRateUnavailableError(
            f"FX response value at '{path}' is not a number: {cursor!r}"
        )
    try:
        return Decimal(str(cursor))
    except (ArithmeticError, ValueError) as exc:
        raise FxRateUnavailableError(
            f"FX response value at '{path}' is not a number: {cursor!r}"
        ) from exc


def reset_api_cache() -> None:
    """Drops the cached rate. For tests, and for a manual refresh."""
    with _api_lock:
        _api_cache.clear()


# -- table ------------------------------------------------------------------


def _table_rate(db: Session | None) -> Decimal:
    # Imported here rather than at module scope: app.models imports pull in
    # the whole model graph, and this module is imported by the fee/quote
    # path on every request regardless of which source is configured.
    from app.models.fx_rate import FxRate

    if db is None:
        raise FxRateUnavailableError(
            "exchange_rate_source='table' needs a database session — pass "
            "db to get_usd_zar_rate()"
        )

    # effective_from <= now, so a row seeded with a future date is a
    # *scheduled* rate change rather than one that takes effect the
    # moment it is inserted. Without the filter, seeding tomorrow's rate
    # silently repriced today's quotes — and broke the append-only
    # table's whole point, which is that a quote can be explained
    # afterwards by the rate that was current when it was issued.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = (
        db.query(FxRate)
        .filter(
            FxRate.currency_pair == FxRate.USD_ZAR,
            FxRate.effective_from <= now,
        )
        .order_by(FxRate.effective_from.desc())
        .first()
    )
    if row is None:
        raise FxRateUnavailableError(
            "No USD/ZAR row in fx_rates effective now. Seed one with: "
            "python -m scripts.seed_fx_rates --rate 18.50"
        )
    return _quantise(Decimal(row.rate))
