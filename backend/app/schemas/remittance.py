"""
Request/response shapes for routers/remittances.py and the cash-out half
of routers/wallet.py.

These are the contract Track 4's frontend and the load tests are written
against, so the quote response carries every figure the brief requires a
quotation to disclose — send amount, rate, fee, margin, the token amount
received, the cash-out fee and the estimated payout — plus the sender's
remaining limit headroom, which is what the quote screen needs to tell
someone *why* their next send was refused.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.remittance import CashOutStatus, RemittanceStatus
from app.services.cashin_cashout_service import CASH_IN_METHODS, payout_currencies
from app.schemas.common import UtcDatetime

# A per-transaction ceiling well above any configured limit. It exists to
# keep a typo ("100000000") out of Numeric(12, 2) rather than to enforce
# policy — policy is UNVERIFIED_*/VERIFIED_* in .env (spec §6).
MAX_SEND_AMOUNT_ZAR = Decimal("1000000.00")

# The same idea for the cash-out leg. Without an upper bound, a value like
# "1e1000" is accepted by Decimal and then blows up inside
# fee_service._token()'s quantize() as decimal.InvalidOperation — which
# routers/wallet.py does not catch, so it surfaced as a 500 on a money
# endpoint. The ceiling matches the column, Numeric(18, 6).
MAX_CASH_OUT_UCTUSD = Decimal("1000000000000.000000")


class QuoteRequest(BaseModel):
    beneficiary_id: uuid.UUID
    # decimal_places is load-bearing, not cosmetic: the limit check in
    # routers/remittances.py runs against the value as submitted, while
    # the row stores fee_service._fiat() of it. Accepting "1000.999"
    # therefore checked 1000.999 against the sender's headroom and then
    # charged them 1001.00 — two different numbers for one request.
    zar_send_amount: Decimal = Field(
        gt=0, le=MAX_SEND_AMOUNT_ZAR, max_digits=12, decimal_places=2
    )


class LimitHeadroom(BaseModel):
    """What the sender has left, after this quote is counted (spec §6)."""

    daily_limit_zar: Decimal
    monthly_limit_zar: Decimal
    daily_remaining_zar: Decimal
    monthly_remaining_zar: Decimal


class QuoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    remittance_id: uuid.UUID
    status: RemittanceStatus
    beneficiary_id: uuid.UUID

    zar_send_amount: Decimal
    # Mid-market ZAR per USD used for this quote.
    fx_rate: Decimal
    transaction_fee_zar: Decimal
    fx_margin_zar: Decimal
    net_converted_zar: Decimal
    uctusd_amount: Decimal
    # All-in ZAR per UCTUSD, fee and margin included — the figure to
    # compare against a bank's quote.
    effective_rate: Decimal

    estimated_payout_currency: str
    cash_out_fee_uctusd: Decimal
    estimated_payout_amount: Decimal

    quote_expires_at: UtcDatetime
    limits: LimitHeadroom


class CashInConfirmRequest(BaseModel):
    cash_in_method: str = Field(
        description="agent_cash | bank_transfer | card",
        examples=["agent_cash"],
    )

    @field_validator("cash_in_method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        normalised = value.strip().lower()
        if normalised not in CASH_IN_METHODS:
            raise ValueError(
                f"cash_in_method must be one of {sorted(CASH_IN_METHODS)}"
            )
        return normalised


class RemittanceRead(BaseModel):
    """
    One remittance as its sender sees it. Carries the settlement hash once
    there is one, so the sender can verify the transfer on a public
    explorer (spec §9.3).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: RemittanceStatus
    beneficiary_id: uuid.UUID

    zar_send_amount: Decimal
    fx_rate_used: Decimal
    transaction_fee_zar: Decimal
    fx_margin_zar: Decimal
    net_converted_zar: Decimal
    uctusd_amount: Decimal

    cash_in_method: str | None
    xrpl_tx_hash: str | None

    created_at: UtcDatetime
    quote_expires_at: UtcDatetime | None
    cash_in_confirmed_at: UtcDatetime | None
    settled_at: UtcDatetime | None


class CashInConfirmResponse(BaseModel):
    remittance: RemittanceRead
    # False when the queue would not take the message: the remittance is
    # confirmed and will be picked up on a retry, so the client should
    # keep polling rather than re-confirming.
    queued: bool
    detail: str


class CashOutRequest(BaseModel):
    uctusd_amount: Decimal = Field(
        gt=0, le=MAX_CASH_OUT_UCTUSD, max_digits=18, decimal_places=6
    )
    payout_currency: str

    @field_validator("payout_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalised = value.strip().upper()
        allowed = payout_currencies()
        if normalised not in allowed:
            raise ValueError(f"payout_currency must be one of {allowed}")
        return normalised


class CashOutRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: CashOutStatus

    uctusd_amount: Decimal
    cash_out_fee_uctusd: Decimal
    net_uctusd: Decimal

    payout_currency: str
    payout_amount: Decimal
    fx_rate_used: Decimal

    failure_reason: str | None

    requested_at: UtcDatetime
    approved_at: UtcDatetime | None
    completed_at: UtcDatetime | None
