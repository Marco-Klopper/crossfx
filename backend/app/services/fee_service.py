"""
Fee and quotation math (spec §4).

Every rate and fee is read from app.config.settings, never hardcoded, per
the brief's requirement that fees and limits stay configurable.

The quotation surfaces, in one object: the ZAR send amount, the mid-market
exchange rate, the transaction fee, the FX margin, the all-in effective
rate, the UCTUSD the recipient will be credited, and — as an estimate —
the cash-out fee and the fiat the recipient would end up with.

The margin is charged ONCE. An earlier draft of this module deducted
fx_margin_zar from the ZAR *and* converted the remainder at a marked-up
rate, which billed the same spread twice and made the disclosed
`fx_margin_zar` line untrue. The customer's all-in rate is therefore
`zar_send_amount / uctusd_amount` — worse than mid-market by exactly the
fee plus the margin, and nothing else.
"""
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from app.config import settings

# Money is stored at 2 decimal places for fiat and 6 for the settlement
# token (Numeric(12, 2) / Numeric(18, 6) — see app/models/remittance.py).
FIAT_QUANTUM = Decimal("0.01")
TOKEN_QUANTUM = Decimal("0.000001")
RATE_QUANTUM = Decimal("0.000001")

SETTLEMENT_CURRENCY = "UCTUSD"
# The corridor's send-side fiat. Named for the same reason
# SETTLEMENT_CURRENCY is: worker/settlement_worker.py used to hardcode
# "ZAR" and "UCTUSD" as string literals, which meant a change to
# SUPPORTED_CURRENCIES could break a ledger write *after* the on-chain
# payment had already gone out.
SEND_CURRENCY = "ZAR"
# UCTUSD is a USD-denominated IOU, so a USD payout is 1:1 by definition;
# ZAR is converted at the same mid-market rate the quote used. Anything
# else would need its own rate feed, which this prototype does not have.
_DIRECT_FROM_UCTUSD = frozenset({"UCTUSD", "USD"})
PRICEABLE_PAYOUT_CURRENCIES = frozenset(_DIRECT_FROM_UCTUSD | {"ZAR"})


class FeeError(Exception):
    """Base class for quotation rejections."""


class AmountTooSmallError(FeeError):
    """
    The fees swallow the whole send amount, so there is nothing left to
    convert. A quote for zero is not a quote.
    """


class UnsupportedPayoutCurrencyError(FeeError):
    """A payout currency this corridor cannot price."""


@dataclass
class Quote:
    zar_send_amount: Decimal
    # Mid-market ZAR per USD, as returned by fx_rate_service.
    fx_rate: Decimal
    transaction_fee_zar: Decimal
    fx_margin_zar: Decimal
    net_converted_zar: Decimal
    uctusd_amount: Decimal
    # All-in ZAR per UCTUSD actually paid, fees and margin included. This
    # is the number a sender should compare against a bank's quote.
    effective_rate: Decimal
    # The recipient's side, quoted up front so the sender can see what
    # actually lands. An estimate: the rate may move before they cash out.
    estimated_payout_currency: str
    cash_out_fee_uctusd: Decimal
    estimated_payout_amount: Decimal


@dataclass
class CashOutQuote:
    uctusd_amount: Decimal
    cash_out_fee_uctusd: Decimal
    net_uctusd: Decimal
    payout_currency: str
    payout_amount: Decimal
    fx_rate: Decimal


def _fiat(amount: Decimal) -> Decimal:
    return amount.quantize(FIAT_QUANTUM, rounding=ROUND_HALF_UP)


def _token(amount: Decimal) -> Decimal:
    # Rounded DOWN: the platform never credits more of the settlement
    # token than the arithmetic supports, because the pooled account has
    # to actually cover every claim in the ledger (spec §9.6).
    return amount.quantize(TOKEN_QUANTUM, rounding=ROUND_DOWN)


def _quantum_for(currency: str) -> Decimal:
    return TOKEN_QUANTUM if currency == SETTLEMENT_CURRENCY else FIAT_QUANTUM


def convert_from_uctusd(
    amount: Decimal, payout_currency: str, usd_zar_rate: Decimal
) -> Decimal:
    """UCTUSD -> a payout currency, at the given mid-market USD/ZAR rate."""
    code = payout_currency.strip().upper()
    if code in _DIRECT_FROM_UCTUSD:
        converted = amount
    elif code == "ZAR":
        converted = amount * usd_zar_rate
    else:
        raise UnsupportedPayoutCurrencyError(
            f"{code} cannot be priced from {SETTLEMENT_CURRENCY} — "
            f"supported: {sorted(PRICEABLE_PAYOUT_CURRENCIES)}"
        )
    return converted.quantize(_quantum_for(code), rounding=ROUND_DOWN)


def calculate_quote(
    zar_send_amount: Decimal,
    usd_zar_rate: Decimal,
    payout_currency: str = "USD",
) -> Quote:
    """
    Prices one remittance. Pure arithmetic — no database, no network, no
    clock — so it is trivially testable and cheap enough to sit on the hot
    path the load tests hammer.
    """
    if zar_send_amount <= 0:
        raise AmountTooSmallError("zar_send_amount must be positive")
    if usd_zar_rate <= 0:
        raise FeeError(f"usd_zar_rate must be positive, got {usd_zar_rate}")

    transaction_fee = _fiat(
        Decimal(str(settings.fixed_remittance_fee_zar))
        + zar_send_amount * Decimal(settings.percent_fee_bps) / Decimal(10_000)
    )
    fx_margin = _fiat(
        zar_send_amount * Decimal(settings.fx_margin_bps) / Decimal(10_000)
    )

    net_converted_zar = _fiat(zar_send_amount - transaction_fee - fx_margin)
    if net_converted_zar <= 0:
        raise AmountTooSmallError(
            f"R{zar_send_amount} does not cover the R{transaction_fee} fee "
            f"and R{fx_margin} margin"
        )

    # Converted at mid-market: the spread is the fx_margin_zar line above,
    # and is not also buried in the rate.
    uctusd_amount = _token(net_converted_zar / usd_zar_rate)
    if uctusd_amount <= 0:
        raise AmountTooSmallError(
            f"R{zar_send_amount} converts to less than the smallest "
            f"{SETTLEMENT_CURRENCY} unit"
        )

    cash_out = calculate_cash_out_payout(
        uctusd_amount, payout_currency, usd_zar_rate
    )

    return Quote(
        zar_send_amount=_fiat(zar_send_amount),
        fx_rate=usd_zar_rate,
        transaction_fee_zar=transaction_fee,
        fx_margin_zar=fx_margin,
        net_converted_zar=net_converted_zar,
        uctusd_amount=uctusd_amount,
        effective_rate=(zar_send_amount / uctusd_amount).quantize(
            RATE_QUANTUM, rounding=ROUND_HALF_UP
        ),
        estimated_payout_currency=cash_out.payout_currency,
        cash_out_fee_uctusd=cash_out.cash_out_fee_uctusd,
        estimated_payout_amount=cash_out.payout_amount,
    )


def calculate_cash_out_payout(
    uctusd_amount: Decimal,
    payout_currency: str,
    usd_zar_rate: Decimal,
) -> CashOutQuote:
    """
    Prices a cash-out (spec §10).

    The fee is taken in the settlement token, before conversion, so the
    recipient is charged the same proportion whichever fiat they pick —
    charging it after conversion would make the fee depend on the payout
    currency for no reason the customer could explain.
    """
    if uctusd_amount <= 0:
        raise AmountTooSmallError("uctusd_amount must be positive")
    if usd_zar_rate <= 0:
        raise FeeError(f"usd_zar_rate must be positive, got {usd_zar_rate}")

    code = payout_currency.strip().upper()
    if code not in PRICEABLE_PAYOUT_CURRENCIES:
        raise UnsupportedPayoutCurrencyError(
            f"{code} cannot be priced from {SETTLEMENT_CURRENCY} — "
            f"supported: {sorted(PRICEABLE_PAYOUT_CURRENCIES)}"
        )

    fee = _token(
        uctusd_amount * Decimal(settings.cashout_fee_bps) / Decimal(10_000)
    )
    net = _token(uctusd_amount - fee)
    if net <= 0:
        raise AmountTooSmallError(
            f"{uctusd_amount} {SETTLEMENT_CURRENCY} does not cover the "
            f"cash-out fee"
        )

    return CashOutQuote(
        uctusd_amount=_token(uctusd_amount),
        cash_out_fee_uctusd=fee,
        net_uctusd=net,
        payout_currency=code,
        payout_amount=convert_from_uctusd(net, code, usd_zar_rate),
        fx_rate=usd_zar_rate,
    )
