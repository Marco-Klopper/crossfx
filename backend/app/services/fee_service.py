"""
Fee and quotation math. All rates/fees are configurable via app.config.settings.

Quotation must surface: ZAR send amount, exchange rate, transaction fee,
FX margin, UCTUSD amount to be received, cash-out fee, estimated payout.
"""
from decimal import Decimal
from dataclasses import dataclass

from app.config import settings


@dataclass
class Quote:
    zar_send_amount: Decimal
    fx_rate: Decimal
    transaction_fee_zar: Decimal
    fx_margin_zar: Decimal
    net_converted_zar: Decimal
    uctusd_amount: Decimal


def calculate_quote(zar_send_amount: Decimal, usd_zar_rate: Decimal) -> Quote:
    transaction_fee = Decimal(settings.fixed_remittance_fee_zar) + (
        zar_send_amount * Decimal(settings.percent_fee_bps) / Decimal(10_000)
    )
    fx_margin = zar_send_amount * Decimal(settings.fx_margin_bps) / Decimal(10_000)

    net_converted_zar = zar_send_amount - transaction_fee - fx_margin
    effective_rate = usd_zar_rate * (1 + Decimal(settings.fx_margin_bps) / Decimal(10_000))
    uctusd_amount = net_converted_zar / effective_rate

    return Quote(
        zar_send_amount=zar_send_amount,
        fx_rate=usd_zar_rate,
        transaction_fee_zar=transaction_fee,
        fx_margin_zar=fx_margin,
        net_converted_zar=net_converted_zar,
        uctusd_amount=uctusd_amount,
    )


def calculate_cash_out_payout(uctusd_amount: Decimal, usd_zar_rate: Decimal) -> Decimal:
    # TODO: apply settings.cashout_fee_bps and return fiat payout amount
    raise NotImplementedError


def check_within_limits(user_daily_total: Decimal, user_monthly_total: Decimal,
                         new_amount: Decimal, is_verified: bool) -> bool:
    daily_limit = Decimal(settings.verified_daily_limit if is_verified else settings.unverified_daily_limit)
    monthly_limit = Decimal(settings.verified_monthly_limit if is_verified else settings.unverified_monthly_limit)
    return (user_daily_total + new_amount <= daily_limit) and (
        user_monthly_total + new_amount <= monthly_limit
    )
