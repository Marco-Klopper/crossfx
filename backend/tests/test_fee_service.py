"""
Unit tests for the fee and quotation math (spec §4, §10).

Pure arithmetic, so these need no database, no client and no fixtures —
just the configured rates, which are pinned per-test where a number
depends on them.
"""
from decimal import Decimal

import pytest

from app.config import settings
from app.services import fee_service
from app.services.fee_service import (
    AmountTooSmallError,
    UnsupportedPayoutCurrencyError,
    calculate_cash_out_payout,
    calculate_quote,
    convert_from_uctusd,
)

RATE = Decimal("18.50")


@pytest.fixture(autouse=True)
def default_rates(monkeypatch):
    """
    Pins the configured fees to their documented defaults, so a teammate
    editing .env cannot silently change what these tests assert.
    """
    monkeypatch.setattr(settings, "fixed_remittance_fee_zar", 25)
    monkeypatch.setattr(settings, "percent_fee_bps", 150)
    monkeypatch.setattr(settings, "fx_margin_bps", 100)
    monkeypatch.setattr(settings, "cashout_fee_bps", 100)


class TestQuote:
    def test_worked_example(self):
        """
        R1 000 at 18.50, with the default fee schedule:
          transaction fee  R25 fixed + 1.50% = R40.00
          FX margin        1.00%             = R10.00
          converted        R1 000 - 50       = R950.00
          received         950 / 18.50       = 51.351351 UCTUSD
        """
        quote = calculate_quote(Decimal("1000.00"), RATE)

        assert quote.transaction_fee_zar == Decimal("40.00")
        assert quote.fx_margin_zar == Decimal("10.00")
        assert quote.net_converted_zar == Decimal("950.00")
        assert quote.uctusd_amount == Decimal("51.351351")

    def test_margin_is_charged_once_not_twice(self):
        """
        The regression this module was rewritten for.

        An earlier version deducted the margin in rand AND converted the
        remainder at a marked-up rate, so the customer paid the spread
        twice and the disclosed fx_margin_zar line understated what they
        were charged. Converting at mid-market means the net rand and the
        UCTUSD credited are two views of the same number.
        """
        quote = calculate_quote(Decimal("1000.00"), RATE)

        round_tripped = quote.uctusd_amount * quote.fx_rate
        assert abs(round_tripped - quote.net_converted_zar) < Decimal("0.01")

        # The double-charged version produced 950 / (18.50 * 1.01) =
        # 50.8429..., which is below every value this assertion allows.
        assert quote.uctusd_amount > Decimal("51")

    def test_effective_rate_is_worse_than_mid_market_by_exactly_the_charges(
        self,
    ):
        quote = calculate_quote(Decimal("1000.00"), RATE)

        assert quote.effective_rate > quote.fx_rate
        # Every rand of the difference is accounted for by the two
        # disclosed lines — there is no third, hidden charge.
        charges = quote.transaction_fee_zar + quote.fx_margin_zar
        assert (
            quote.zar_send_amount - charges == quote.net_converted_zar
        )
        assert abs(
            quote.effective_rate * quote.uctusd_amount
            - quote.zar_send_amount
        ) < Decimal("0.01")

    def test_scales_with_the_send_amount(self):
        small = calculate_quote(Decimal("500.00"), RATE)
        large = calculate_quote(Decimal("5000.00"), RATE)

        # The fixed R25 is a smaller share of a larger send, so the
        # effective rate improves with size.
        assert large.effective_rate < small.effective_rate

    def test_carries_the_recipients_side(self):
        quote = calculate_quote(Decimal("1000.00"), RATE, "ZAR")

        assert quote.estimated_payout_currency == "ZAR"
        assert quote.cash_out_fee_uctusd == Decimal("0.513513")
        assert quote.estimated_payout_amount == Decimal("940.50")

    def test_defaults_the_payout_estimate_to_usd(self):
        assert (
            calculate_quote(Decimal("1000.00"), RATE).estimated_payout_currency
            == "USD"
        )

    @pytest.mark.parametrize("amount", ["0", "-100"])
    def test_rejects_a_non_positive_amount(self, amount):
        with pytest.raises(AmountTooSmallError):
            calculate_quote(Decimal(amount), RATE)

    def test_rejects_an_amount_the_fees_swallow(self):
        """R20 cannot cover a R25 fixed fee — quoting zero would be a lie."""
        with pytest.raises(AmountTooSmallError):
            calculate_quote(Decimal("20.00"), RATE)

    def test_rejects_a_non_positive_rate(self):
        with pytest.raises(fee_service.FeeError):
            calculate_quote(Decimal("1000.00"), Decimal("0"))


class TestCashOutPayout:
    def test_fee_is_taken_in_the_settlement_token(self):
        payout = calculate_cash_out_payout(
            Decimal("100.000000"), "USD", RATE
        )

        assert payout.cash_out_fee_uctusd == Decimal("1.000000")
        assert payout.net_uctusd == Decimal("99.000000")
        assert payout.payout_amount == Decimal("99.00")

    def test_converts_to_rand_at_the_given_rate(self):
        payout = calculate_cash_out_payout(
            Decimal("100.000000"), "ZAR", RATE
        )

        assert payout.net_uctusd == Decimal("99.000000")
        assert payout.payout_amount == Decimal("1831.50")

    def test_fee_is_the_same_proportion_whichever_currency_is_chosen(self):
        """
        Charging the fee before conversion is what makes this true. If it
        were charged after, the fee would depend on the payout currency
        for no reason a customer could explain.
        """
        usd = calculate_cash_out_payout(Decimal("100.000000"), "USD", RATE)
        zar = calculate_cash_out_payout(Decimal("100.000000"), "ZAR", RATE)

        assert usd.cash_out_fee_uctusd == zar.cash_out_fee_uctusd

    def test_rejects_an_unpriceable_currency(self):
        with pytest.raises(UnsupportedPayoutCurrencyError):
            calculate_cash_out_payout(Decimal("100"), "EUR", RATE)

    def test_rejects_a_non_positive_amount(self):
        with pytest.raises(AmountTooSmallError):
            calculate_cash_out_payout(Decimal("0"), "USD", RATE)

    def test_rejects_an_amount_below_the_fee(self, monkeypatch):
        monkeypatch.setattr(settings, "cashout_fee_bps", 10_000)  # 100%
        with pytest.raises(AmountTooSmallError):
            calculate_cash_out_payout(Decimal("1.000000"), "USD", RATE)


class TestConversion:
    def test_uctusd_and_usd_are_one_to_one(self):
        assert convert_from_uctusd(
            Decimal("42.123456"), "USD", RATE
        ) == Decimal("42.12")

    def test_rounds_down_never_up(self):
        """
        The pooled account has to cover every claim in the ledger
        (spec §9.6), so rounding in the customer's favour is not free —
        it is a shortfall the platform funds.
        """
        assert convert_from_uctusd(
            Decimal("0.999999"), "USD", RATE
        ) == Decimal("0.99")

    def test_is_case_and_whitespace_insensitive(self):
        assert convert_from_uctusd(
            Decimal("1.000000"), " zar ", RATE
        ) == Decimal("18.50")
