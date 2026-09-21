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
