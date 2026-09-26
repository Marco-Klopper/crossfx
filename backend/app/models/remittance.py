"""
A single remittance transaction: ZAR in, UCTUSD settled to the recipient's wallet.

Status flow (roughly):
  quoted -> cash_in_confirmed -> queued -> settling -> settled -> (cashed_out) | failed

A quote is persisted as a QUOTED row rather than held in memory, so that a
sender's outstanding quotes count against their limits (spec §6) and so the
id they confirm cash-in against is a real record. `quote_expires_at` is what
stops an unfunded quote holding that headroom forever.

The cash-out at the end of the journey is NOT a column here: it spends a
ledger *balance*, not a particular remittance, so it is its own record —
`CashOut`, below (spec §10).
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RemittanceStatus(str, enum.Enum):
    QUOTED = "quoted"
    CASH_IN_CONFIRMED = "cash_in_confirmed"
    QUEUED = "queued"
    SETTLING = "settling"
    SETTLED = "settled"
    FAILED = "failed"
    # FAILED used to be the end of the line: the sender's rand had been
    # taken, the on-chain leg had not landed, and no route through the
    # API returned the money or retried the transfer. REFUNDED is that
    # missing terminal state — reached through
    # POST /admin/remittances/{id}/refund, which writes the sender's
    # ZAR back as a ledger entry before flipping the status.
    REFUNDED = "refunded"


class CashOutStatus(str, enum.Enum):
    """
    A cash-out's own lifecycle. Separate from RemittanceStatus on purpose:
    the two are different objects with different operators — a remittance
    is progressed by the settlement worker, a cash-out by an admin.
    """

    REQUESTED = "requested"
    APPROVED = "approved"
    # Claimed by the worker: the burn is being submitted. Kept distinct
    # from APPROVED so a redelivered message cannot burn twice.
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# The statuses whose amounts are "spoken for" and so consume a sender's
# limit headroom.
#
# FAILED is in the list, which reads oddly until you look at when it is
# reached. This tuple used to omit it, reasoning that "nothing left the
# sender" — but a settlement can only fail *after* cash-in was confirmed,
# so the sender's rand had in fact already gone. Omitting it handed the
# headroom straight back while the money was still missing. A failed
# remittance therefore keeps consuming headroom until it is REFUNDED,
# which is the point at which the money genuinely is back.
#
# Two conditions this tuple cannot express are applied in
# app.services.limits_service instead: an expired QUOTED does not consume
# headroom, and neither does a FAILED row whose cash-in was never
# confirmed.
LIMIT_CONSUMING_STATUSES = (
    RemittanceStatus.QUOTED,
    RemittanceStatus.CASH_IN_CONFIRMED,
    RemittanceStatus.QUEUED,
    RemittanceStatus.SETTLING,
    RemittanceStatus.SETTLED,
    RemittanceStatus.FAILED,
)


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
    uctusd_amount = Column(Numeric(18, 6), nullable=False)

    status = Column(Enum(RemittanceStatus), default=RemittanceStatus.QUOTED, nullable=False)

    xrpl_tx_hash = Column(String, nullable=True)
    cash_in_method = Column(String, nullable=True)  # agent_cash | bank_transfer | card

    created_at = Column(DateTime, default=_utcnow)
    # When the quoted rate and fees stop being honoured. Null once the
    # quote has been funded — the figures are locked in at that point.
    quote_expires_at = Column(DateTime, nullable=True)
    cash_in_confirmed_at = Column(DateTime, nullable=True)
    settled_at = Column(DateTime, nullable=True)

    # Physical treasury settlement (spec 9.4) —  batched treasury settlement; many rows may share one value.
    treasury_batch_id = Column(String, nullable=True)
    treasury_settled_at = Column(DateTime, nullable=True)

    sender = relationship("User", back_populates="remittances")
    beneficiary = relationship("Beneficiary", back_populates="remittances")

    __table_args__ = (
        # app.services.limits_service.sender_totals filters on exactly
        # this pair on every single quote request, and nothing indexed
        # either column. It is the first query that would degrade under
        # the load tests.
        Index("ix_remittances_sender_id_created_at", "sender_id", "created_at"),
    )

    def is_quote_expired(self, now: datetime | None = None) -> bool:
        """
        Whether an unfunded quote has aged out. Only QUOTED rows can
        expire; once cash-in is confirmed the figures are committed.

        SQLite hands back naive datetimes even for values written as
        aware ones, so the stored value is treated as UTC when it has no
        tzinfo rather than being compared against an aware `now` and
        raising.
        """
        if self.status != RemittanceStatus.QUOTED or self.quote_expires_at is None:
            return False
        expires_at = self.quote_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return (now or _utcnow()) >= expires_at


class CashOut(Base):
    """
    A recipient converting held UCTUSD into fiat (spec §10).

    It belongs to a *user*, not a remittance: by the time value is cashed
    out it has been pooled into one ledger balance, and asking which
    remittance a given rand came from is a question the ledger cannot
    answer and does not need to.

    The UCTUSD is debited when the cash-out is requested, not when it is
    approved — otherwise a recipient could request three cash-outs
    against the same balance and have all three approved. The two ledger
    entries are recorded here so the audit trail runs both ways.
    """

    __tablename__ = "cash_outs"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False)

    uctusd_amount = Column(Numeric(18, 6), nullable=False)
    cash_out_fee_uctusd = Column(Numeric(18, 6), nullable=False)
    net_uctusd = Column(Numeric(18, 6), nullable=False)

    payout_currency = Column(String, nullable=False)  # USD | ZAR | UCTUSD
    payout_amount = Column(Numeric(18, 6), nullable=False)
    fx_rate_used = Column(Numeric(12, 6), nullable=False)

    status = Column(
        Enum(CashOutStatus), default=CashOutStatus.REQUESTED, nullable=False
    )
    failure_reason = Column(String, nullable=True)

    # The ledger entries behind the two legs: UCTUSD out on request, fiat
    # in on completion. Nullable because the second does not exist yet
    # while the cash-out is pending, and neither exists on a refund.
    debit_transaction_id = Column(
        Uuid, ForeignKey("wallet_transactions.id"), nullable=True
    )
    credit_transaction_id = Column(
        Uuid, ForeignKey("wallet_transactions.id"), nullable=True
    )

    # Supplied by the client as an Idempotency-Key header. Without it a
    # double-clicked "Request payout" button debited the balance twice
    # and opened two cash-outs: unlike the settlement path there was no
    # key, no unique constraint and no dedupe of any kind. Nullable
    # because the header is optional, and NULLs are distinct under the
    # unique index in both SQLite and Postgres, so unkeyed requests are
    # unaffected.
    idempotency_key = Column(String, nullable=True)

    # The on-chain burn (a payment of the net UCTUSD back to the issuer,
    # standing in for the hand-over to an exchange). Null until the
    # worker has submitted it, and forever on a rejected cash-out.
    xrpl_tx_hash = Column(String, nullable=True)

    # Who released or refused the payout. KYC already records its
    # reviewer; a money movement had no such trail at all.
    reviewed_by_admin_id = Column(
        Uuid, ForeignKey("users.id"), nullable=True
    )

    requested_at = Column(DateTime, default=_utcnow, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_cash_outs_idempotency_key", "idempotency_key", unique=True),
        Index("ix_cash_outs_user_id", "user_id"),
    )

    user = relationship(
        "User", back_populates="cash_outs", foreign_keys=[user_id]
    )
