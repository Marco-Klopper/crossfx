"""
A single remittance transaction: ZAR in, RLUSD settled to the recipient's wallet.

Status flow (roughly):
  quoted -> cash_in_confirmed -> queued -> settling -> settled -> (cashed_out) | failed
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Numeric, String, Uuid

from app.database import Base


class RemittanceStatus(str, enum.Enum):
    QUOTED = "quoted"
    CASH_IN_CONFIRMED = "cash_in_confirmed"
    QUEUED = "queued"
    SETTLING = "settling"
    SETTLED = "settled"
    FAILED = "failed"


class Remittance(Base):
    __tablename__ = "remittances"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key = Column(String, unique=True, nullable=False)  # prevents duplicate queue credits

    sender_id = Column(Uuid, ForeignKey("users.id"), nullable=False)
    beneficiary_id = Column(Uuid, ForeignKey("beneficiaries.id"), nullable=False)

    zar_send_amount = Column(Numeric(12, 2), nullable=False)
    fx_rate_used = Column(Numeric(12, 6), nullable=False)
    transaction_fee_zar = Column(Numeric(12, 2), nullable=False)
    fx_margin_zar = Column(Numeric(12, 2), nullable=False)
    net_converted_zar = Column(Numeric(12, 2), nullable=False)
    rlusd_amount = Column(Numeric(18, 6), nullable=False)

    status = Column(Enum(RemittanceStatus), default=RemittanceStatus.QUOTED, nullable=False)

    xrpl_tx_hash = Column(String, nullable=True)
    cash_in_method = Column(String, nullable=True)  # agent_cash | bank_transfer | card

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    settled_at = Column(DateTime, nullable=True)

    # Physical treasury settlement (spec 9.4) —  batched trreasury settlement; many rows may share one value.
    treasury_batch_id = Column(String, nullable=True)
    treasury_settled_at = Column(DateTime, nullable=True)

    # TODO: add cash-out sub-record (status: requested/approved/completed/failed,
    # payout currency, cash-out fee, final fiat amount)
