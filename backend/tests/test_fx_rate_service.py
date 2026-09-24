"""
Tests for the three exchange-rate sources (spec §5).

The `api` source is exercised against a stub transport rather than the
internet: a test that needs a third party to be up is a test that fails
for reasons that have nothing to do with the code.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import settings
from app.models.fx_rate import FxRate
from app.services import fx_rate_service
from app.services.fx_rate_service import (
    FxRateUnavailableError,
    get_usd_zar_rate,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """The API cache is module-level, so it has to be cleared per test."""
    fx_rate_service.reset_api_cache()
    yield
    fx_rate_service.reset_api_cache()


class _StubResponse:
    def __init__(self, payload, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self):
        return self._payload


class TestSourceSelection:
    def test_rejects_an_unknown_source(self, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_source", "carrier-pigeon")
        with pytest.raises(ValueError, match="carrier-pigeon"):
            get_usd_zar_rate()


class TestMockSource:
    @pytest.fixture(autouse=True)
    def use_mock(self, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_source", "mock")
        monkeypatch.setattr(settings, "fx_mock_base_rate", 18.50)
        monkeypatch.setattr(settings, "fx_mock_volatility_bps", 150)
        monkeypatch.setattr(settings, "fx_rate_cache_seconds", 300)

    def test_is_stable_within_a_bucket(self):
        """
        A sender must not be shown one rate on the quote screen and
        charged another a second later, so the rate only moves once per
        cache bucket.
        """
        bucket_start = (1_700_000_000 // 300) * 300
        first = fx_rate_service._mock_rate(now=bucket_start)
        second = fx_rate_service._mock_rate(now=bucket_start + 299)
        assert first == second

    def test_moves_between_buckets(self):
        """Not a constant: a rate that never moves proves nothing in a demo."""
        rates = {
            fx_rate_service._mock_rate(now=1_700_000_000 + bucket * 300)
            for bucket in range(12)
        }
        assert len(rates) > 1

    def test_stays_inside_the_configured_band(self):
        band = Decimal("18.50") * Decimal("150") / Decimal("10000")
        for bucket in range(40):
            rate = fx_rate_service._mock_rate(now=bucket * 300)
            assert abs(rate - Decimal("18.50")) <= band

    def test_zero_volatility_pins_the_rate_flat(self, monkeypatch):
        """What a scripted demo wants: a rate that cannot surprise you."""
        monkeypatch.setattr(settings, "fx_mock_volatility_bps", 0)
        assert get_usd_zar_rate() == Decimal("18.500000")

    def test_needs_no_database(self):
        assert get_usd_zar_rate(None) > 0


class TestApiSource:
    @pytest.fixture(autouse=True)
    def use_api(self, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_source", "api")
        monkeypatch.setattr(settings, "fx_api_rate_path", "rates.ZAR")
        monkeypatch.setattr(settings, "fx_rate_cache_seconds", 300)

    def _stub(self, monkeypatch, payload, error=None):
        calls = []

        def fake_get(url, timeout=None):
            calls.append(url)
            return _StubResponse(payload, error)

        monkeypatch.setattr(fx_rate_service.httpx, "get", fake_get)
        return calls

    def test_reads_the_configured_path(self, monkeypatch):
        self._stub(monkeypatch, {"rates": {"ZAR": 18.42, "EUR": 0.9}})
        assert get_usd_zar_rate() == Decimal("18.420000")

    def test_caches_so_quoting_stays_pure_compute(self, monkeypatch):
        """
        performance-testing/README.md characterises /remittances/quote as
        pure compute. One HTTP request per quote would make this service
        the bottleneck the load test is meant to measure around.
        """
        calls = self._stub(monkeypatch, {"rates": {"ZAR": 18.42}})
        for _ in range(5):
            get_usd_zar_rate()
        assert len(calls) == 1

    def test_serves_the_last_good_rate_when_a_refresh_fails(
        self, monkeypatch
    ):
        self._stub(monkeypatch, {"rates": {"ZAR": 18.42}})
        assert get_usd_zar_rate() == Decimal("18.420000")

        def exploding_get(url, timeout=None):
            raise ConnectionError("provider is down")

        monkeypatch.setattr(fx_rate_service.httpx, "get", exploding_get)
        monkeypatch.setattr(settings, "fx_rate_cache_seconds", 0)

        # Stale but real beats refusing to quote because a third party
        # blinked.
        assert get_usd_zar_rate() == Decimal("18.420000")

    def test_raises_when_there_is_no_rate_to_fall_back_on(self, monkeypatch):
        def exploding_get(url, timeout=None):
            raise ConnectionError("provider is down")

        monkeypatch.setattr(fx_rate_service.httpx, "get", exploding_get)
        with pytest.raises(FxRateUnavailableError):
            get_usd_zar_rate()

    def test_raises_on_a_response_missing_the_path(self, monkeypatch):
        self._stub(monkeypatch, {"rates": {"EUR": 0.9}})
        with pytest.raises(FxRateUnavailableError, match="rates.ZAR"):
            get_usd_zar_rate()

    def test_raises_on_a_non_numeric_value(self, monkeypatch):
        self._stub(monkeypatch, {"rates": {"ZAR": "not-a-number"}})
        with pytest.raises(FxRateUnavailableError):
            get_usd_zar_rate()

    def test_raises_on_a_non_positive_rate(self, monkeypatch):
        self._stub(monkeypatch, {"rates": {"ZAR": 0}})
        with pytest.raises(FxRateUnavailableError):
            get_usd_zar_rate()

    def test_propagates_an_http_error(self, monkeypatch):
        self._stub(
            monkeypatch, {}, error=RuntimeError("503 Service Unavailable")
        )
        with pytest.raises(FxRateUnavailableError):
            get_usd_zar_rate()


class TestTableSource:
    @pytest.fixture(autouse=True)
    def use_table(self, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_source", "table")

    def test_reads_the_most_recent_row(self, db_session):
        now = datetime.now(timezone.utc)
        db_session.add_all(
            [
                FxRate(
                    currency_pair=FxRate.USD_ZAR,
                    rate=Decimal("17.00"),
                    effective_from=now - timedelta(days=2),
                ),
                FxRate(
                    currency_pair=FxRate.USD_ZAR,
                    rate=Decimal("19.25"),
                    effective_from=now,
                ),
            ]
        )
        db_session.commit()

        assert get_usd_zar_rate(db_session) == Decimal("19.250000")

    def test_ignores_other_pairs(self, db_session):
        db_session.add(
            FxRate(
                currency_pair="USD/EUR",
                rate=Decimal("0.90"),
                effective_from=datetime.now(timezone.utc),
            )
        )
        db_session.commit()

        with pytest.raises(FxRateUnavailableError, match="seed_fx_rates"):
            get_usd_zar_rate(db_session)

    def test_explains_how_to_seed_an_empty_table(self, db_session):
        with pytest.raises(FxRateUnavailableError, match="seed_fx_rates"):
            get_usd_zar_rate(db_session)

    def test_requires_a_session(self):
        with pytest.raises(FxRateUnavailableError, match="database session"):
            get_usd_zar_rate(None)


class TestStaleFallbackCeiling:
    """
    Serving the last good rate when a refresh fails is right. Serving it
    for ever is not: the fallback never refreshed its expiry, so a
    provider down for two days meant quotes priced on a two-day-old rate,
    with a log line as the only sign.
    """

    @pytest.fixture()
    def clock(self, monkeypatch):
        """A clock the test can wind forward."""
        current = {"t": 1_000_000.0}
        monkeypatch.setattr(
            fx_rate_service.time, "time", lambda: current["t"]
        )
        return current

    @pytest.fixture(autouse=True)
    def api_source(self, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_source", "api")
        monkeypatch.setattr(settings, "fx_rate_cache_seconds", 300)
        monkeypatch.setattr(settings, "fx_rate_max_stale_seconds", 3600)
        fx_rate_service.reset_api_cache()
        yield
        fx_rate_service.reset_api_cache()

    @staticmethod
    def _break_the_provider(monkeypatch):
        def boom():
            raise RuntimeError("provider is down")

        monkeypatch.setattr(fx_rate_service, "_fetch_rate", boom)

    def test_a_recent_rate_is_still_served_when_the_provider_blips(
        self, monkeypatch, clock
    ):
        monkeypatch.setattr(
            fx_rate_service, "_fetch_rate", lambda: Decimal("18.50")
        )
        assert get_usd_zar_rate() == Decimal("18.500000")

        # Past the cache window, but well inside the staleness ceiling.
        clock["t"] += 400
        self._break_the_provider(monkeypatch)

        assert get_usd_zar_rate() == Decimal("18.500000")

    def test_a_rate_older_than_the_ceiling_is_refused(
        self, monkeypatch, clock
    ):
        monkeypatch.setattr(
            fx_rate_service, "_fetch_rate", lambda: Decimal("18.50")
        )
        assert get_usd_zar_rate() == Decimal("18.500000")

        # Two days later, with the provider still down.
        clock["t"] += 172_800
        self._break_the_provider(monkeypatch)

        with pytest.raises(FxRateUnavailableError) as caught:
            get_usd_zar_rate()
        assert "older than" in str(caught.value)

    def test_the_fallback_does_not_extend_its_own_freshness(
        self, monkeypatch, clock
    ):
        """
        The bug underneath the missing ceiling: each failed refresh used
        to re-serve the cached value without ever recording how old it
        was, so age never accumulated.
        """
        monkeypatch.setattr(
            fx_rate_service, "_fetch_rate", lambda: Decimal("18.50")
        )
        assert get_usd_zar_rate() == Decimal("18.500000")
        self._break_the_provider(monkeypatch)

        # Several failed refreshes inside the ceiling...
        for _ in range(3):
            clock["t"] += 600
            assert get_usd_zar_rate() == Decimal("18.500000")

        # ...still age the rate, rather than resetting the clock on it.
        clock["t"] += 3600
        with pytest.raises(FxRateUnavailableError):
            get_usd_zar_rate()


class TestTableSourceEffectiveFrom:
    """
    The fx_rates table is append-only so that a quote can be explained
    afterwards by the rate that was current when it was issued. A row
    dated in the future used to take effect immediately, which breaks
    exactly that.
    """

    @pytest.fixture(autouse=True)
    def table_source(self, monkeypatch):
        monkeypatch.setattr(settings, "exchange_rate_source", "table")

    def _seed(self, db_session, rate, effective_from):
        from app.models.fx_rate import FxRate

        row = FxRate(
            currency_pair=FxRate.USD_ZAR,
            rate=Decimal(rate),
            effective_from=effective_from,
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_a_current_row_is_used(self, db_session):
        self._seed(
            db_session, "18.50", datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        )
        assert fx_rate_service.get_usd_zar_rate(db_session) == Decimal(
            "18.500000"
        )

    def test_a_future_row_does_not_take_effect_yet(self, db_session):
        self._seed(
            db_session, "18.50", datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        )
        self._seed(
            db_session, "25.00", datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        )
        # Tomorrow's scheduled rate must not reprice today's quotes.
        assert fx_rate_service.get_usd_zar_rate(db_session) == Decimal(
            "18.500000"
        )

    def test_only_future_rows_is_treated_as_no_rate(self, db_session):
        self._seed(
            db_session, "25.00", datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
        )
        with pytest.raises(fx_rate_service.FxRateUnavailableError):
            fx_rate_service.get_usd_zar_rate(db_session)
