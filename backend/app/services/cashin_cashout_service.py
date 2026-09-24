"""
Simulated ZAR cash-in confirmation and fiat cash-out processing
(spec §8 and §10).

No real payment rails are touched. A production build would replace
`simulate_cash_in` with a webhook from a PSP and `simulate_cash_out` with
a payout instruction to a bank or agent network; everything either side of
those two functions — the state machine, the ledger entries, the queue
handoff — is the real thing and would not change.

Nothing here commits. The caller owns the transaction boundary, for the
same reason app.services.ledger does: a status change and the ledger entry
behind it have to land together or not at all.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.models.beneficiary import Beneficiary
from app.models.remittance import CashOut, CashOutStatus, Remittance, RemittanceStatus
from app.models.user import User
from app.services import fee_service
from app.services.ledger import Direction, InsufficientFundsError, Ledger
from app.services.settlement_queue import SettlementQueue

logger = logging.getLogger(__name__)

# The three ways a sender can hand over rand in this corridor. Agent cash
# is the one that matters for the brief's target user: no bank account.
CASH_IN_METHODS = frozenset({"agent_cash", "bank_transfer", "card"})

# What a recipient may cash out into. Narrower than
# schemas/beneficiary.ALLOWED_PAYOUT_CURRENCIES, and deliberately so: the
# internal ledger only accepts SUPPORTED_CURRENCIES, and fee_service can
# only price the pair it has a rate for. UCTUSD is excluded because
# "cashing out" into the token you already hold is a no-op.
def payout_currencies() -> list[str]:
    return sorted(
        set(settings.supported_currencies_list)
        & (
            fee_service.PRICEABLE_PAYOUT_CURRENCIES
            - {fee_service.SETTLEMENT_CURRENCY}
        )
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CashInError(Exception):
    """Base class for cash-in rejections."""


class InvalidCashInMethodError(CashInError):
    pass


class QuoteExpiredError(CashInError):
    """The quoted rate and fees are no longer honoured (spec §5)."""


class CashInStateError(CashInError):
    """The remittance is past the point where cash-in can be confirmed."""


class RecipientNotRegisteredError(CashInError):
    """
    No CrossFX account matches the beneficiary's contact details. Under
    pooled custody the credit lands in the recipient's internal wallet,
    which they must own before value can be sent to it (spec §9.1).
    """


class RefundStateError(CashInError):
    """A remittance that cannot be refunded from the state it is in."""


class SettlementPublishError(Exception):
    """
    The queue would not take the message. The remittance is left
    CASH_IN_CONFIRMED, which is a re-publishable state — see
    routers/admin.py's confirm-payment.
    """


class CashOutError(Exception):
    """Base class for cash-out rejections."""


class CashOutStateError(CashOutError):
    pass


# -- cash-in (spec §8) ------------------------------------------------------


def simulate_cash_in(
    db: Session, remittance: Remittance, method: str, now: datetime | None = None
) -> bool:
    """
    Mock confirmation that the sender's rand arrived.

    Returns True when this call is what confirmed it, and False when it
    was already confirmed — so a double-submitted form is a no-op rather
    than an error, and the caller can still safely (re)publish.
    """
    normalised = method.strip().lower()
    if normalised not in CASH_IN_METHODS:
        raise InvalidCashInMethodError(
            f"cash_in_method must be one of {sorted(CASH_IN_METHODS)}"
        )

    if remittance.status in (
        RemittanceStatus.CASH_IN_CONFIRMED,
        RemittanceStatus.QUEUED,
    ):
        return False
    if remittance.status != RemittanceStatus.QUOTED:
        raise CashInStateError(
            f"Remittance is {remittance.status.value}; cash-in can only be "
            f"confirmed on a quote"
        )
    if remittance.is_quote_expired(now):
        raise QuoteExpiredError(
            "This quote has expired — request a new one before paying in"
        )
    # Checked here, at the point money changes hands, rather than at quote
    # time: taking a sender's cash for a transfer that cannot land is the
    # failure worth preventing, and quoting is only pricing.
    assert_recipient_registered(db, remittance)

    remittance.cash_in_method = normalised
    remittance.status = RemittanceStatus.CASH_IN_CONFIRMED
    remittance.cash_in_confirmed_at = now or _utcnow()
    # The figures are locked in now, so there is nothing left to expire.
    remittance.quote_expires_at = None
    db.flush()
    return True


def assert_recipient_registered(db: Session, remittance: Remittance) -> User:
    """
    The registered user whose wallet a settlement would credit.

    Resolved the same way worker/settlement_worker.py resolves it — by
    matching Beneficiary.contact against User.email — so a remittance that
    passes here is one the worker can actually settle.
    """
    beneficiary = db.get(Beneficiary, remittance.beneficiary_id)
    if beneficiary is None:
        raise RecipientNotRegisteredError(
            "This remittance has no beneficiary on record"
        )
    recipient = (
        db.query(User).filter(User.email == beneficiary.contact).first()
    )
    if recipient is None:
        raise RecipientNotRegisteredError(
            f"{beneficiary.contact} has no CrossFX account yet — the "
            f"recipient must register before this transfer can be paid in"
        )
    return recipient


def queue_for_settlement(
    db: Session,
    remittance: Remittance,
    queue: SettlementQueue | None = None,
) -> str:
    """
    Step 2 of the brief's async flow: hand the remittance to the
    settlement queue and mark it QUEUED.

    Call this only after the CASH_IN_CONFIRMED status has been committed.
    Publishing first and committing after would let a crash in between
    leave a message pointing at a remittance the worker will refuse to
    claim; this ordering can only ever produce the harmless opposite — a
    confirmed remittance whose message is republished.
    """
    if remittance.status not in (
        RemittanceStatus.CASH_IN_CONFIRMED,
        RemittanceStatus.QUEUED,
    ):
        raise CashInStateError(
            f"Remittance is {remittance.status.value}; only a confirmed "
            f"cash-in can be queued"
        )

    try:
        entry_id = (queue or SettlementQueue()).publish(
            remittance.idempotency_key, remittance.id
        )
    except Exception as exc:
        logger.exception(
            "Could not publish settlement for %s — left CASH_IN_CONFIRMED "
            "for retry",
            remittance.id,
        )
        raise SettlementPublishError(str(exc)) from exc

    remittance.status = RemittanceStatus.QUEUED
    db.flush()
    logger.info(
        "Queued %s for settlement (entry %s)", remittance.id, entry_id
    )
    return entry_id


# -- failure recovery -------------------------------------------------------
#
# A settlement can only fail after cash-in was confirmed, so a FAILED
# remittance always means the sender's rand is gone and the recipient was
# never credited. There used to be no way out of that state: the admin
# retry endpoint refused it, no refund existed, and limits_service handed
# the headroom back as though nothing had happened. These two functions
# are the two legitimate exits — try again, or give the money back.


def requeue_failed_remittance(db: Session, remittance: Remittance) -> None:
    """
    Returns a FAILED remittance to CASH_IN_CONFIRMED so it can be
    published again.

    The cash-in itself is not re-simulated: the sender already paid, and
    the timestamps recording that stay exactly as they are. Only the
    status moves, which is what makes the row claimable by the worker
    again (worker.CLAIMABLE).
    """
    if remittance.status != RemittanceStatus.FAILED:
        raise CashInStateError(
            f"Remittance is {remittance.status.value}; only a failed "
            f"settlement can be requeued"
        )
    if remittance.cash_in_confirmed_at is None:
        raise CashInStateError(
            "This remittance failed before cash-in was confirmed, so there "
            "is nothing to settle — the sender must request a new quote"
        )
    # Still the precondition it was at cash-in: a transfer cannot land on
    # a recipient who has since gone away.
    assert_recipient_registered(db, remittance)

    remittance.status = RemittanceStatus.CASH_IN_CONFIRMED
    db.flush()
    logger.info("Requeued failed remittance %s for settlement", remittance.id)


def refund_failed_remittance(
    db: Session, remittance: Remittance, admin: User, now: datetime | None = None
) -> Remittance:
    """
    Gives a failed remittance's rand back and marks it REFUNDED.

    The credit is a `record_external` entry rather than a balance
    movement, for the same reason the sender's outgoing leg is: their ZAR
    never sat in a ledger balance — it went bank/agent -> corridor pool —
    so the refund leaves by the same door it came in. The entry is what
    the sender's transaction history shows, and it is the audit trail for
    money that moved outside the ledger.

    REFUNDED is deliberately absent from LIMIT_CONSUMING_STATUSES: this
    is the moment the sender's headroom is genuinely free again.
    """
    if remittance.status != RemittanceStatus.FAILED:
        raise RefundStateError(
            f"Remittance is {remittance.status.value}; only a failed "
            f"settlement can be refunded"
        )
    if remittance.cash_in_confirmed_at is None:
        raise RefundStateError(
            "This remittance failed before cash-in was confirmed, so no "
            "money was ever taken from the sender"
        )

    sender = db.get(User, remittance.sender_id)
    if sender is None:
        raise RefundStateError(
            "This remittance has no sender on record to refund"
        )

    ledger = Ledger(db)
    ledger.record_external(
        ledger.wallet_for(sender),
        Direction.INCOMING,
        fee_service.SEND_CURRENCY,
        Decimal(remittance.zar_send_amount),
        remittance_id=remittance.id,
    )

    remittance.status = RemittanceStatus.REFUNDED
    db.flush()
    logger.info(
        "Refunded %s %s to %s for failed remittance %s (admin %s)",
        remittance.zar_send_amount,
        fee_service.SEND_CURRENCY,
        sender.email,
        remittance.id,
        admin.id,
    )
    return remittance


# -- cash-out (spec §10) ----------------------------------------------------


def request_cash_out(
    db: Session,
    user: User,
    uctusd_amount: Decimal,
    payout_currency: str,
    usd_zar_rate: Decimal,
    idempotency_key: str | None = None,
) -> CashOut:
    """
    Opens a cash-out and debits the UCTUSD immediately.

    Debiting on request rather than on approval is what stops a recipient
    opening three cash-outs against one balance and having all three
    approved. `Ledger.debit` refusing to go negative is the brief's
    "validate sufficient balance" step, so there is no separate check.

    `idempotency_key` comes from the caller's Idempotency-Key header. It
    is what makes a retried or double-clicked request a no-op: without
    it, this function happily debited the same balance twice and opened
    two payouts, which was the most likely way for a user to lose money
    in this system. A replay returns the original row untouched.
    """
    if idempotency_key is not None:
        existing = (
            db.query(CashOut)
            .filter(
                CashOut.user_id == user.id,
                CashOut.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            logger.info(
                "Cash-out %s replayed under idempotency key %s — returning "
                "the original",
                existing.id,
                idempotency_key,
            )
            return existing

    quote = fee_service.calculate_cash_out_payout(
        uctusd_amount, payout_currency, usd_zar_rate
    )
    # Not redundant with calculate_cash_out_payout above, which prices
    # anything in PRICEABLE_PAYOUT_CURRENCIES — a set that includes
    # UCTUSD. payout_currencies() excludes it, because cashing out into
    # the token you already hold is a no-op. The API's own schema
    # validator rejects it first, so this only fires for a direct caller.
    if quote.payout_currency not in payout_currencies():
        raise fee_service.UnsupportedPayoutCurrencyError(
            f"{quote.payout_currency} is not a supported payout currency — "
            f"supported: {payout_currencies()}"
        )

    ledger = Ledger(db)
    wallet = ledger.wallet_for(user)
    # Propagates InsufficientFundsError to the caller, which maps it to a
    # 400 with the shortfall in the message.
    debit = ledger.debit(
        wallet, fee_service.SETTLEMENT_CURRENCY, quote.uctusd_amount
    )

    cash_out = CashOut(
        user_id=user.id,
        uctusd_amount=quote.uctusd_amount,
        cash_out_fee_uctusd=quote.cash_out_fee_uctusd,
        net_uctusd=quote.net_uctusd,
        payout_currency=quote.payout_currency,
        payout_amount=quote.payout_amount,
        fx_rate_used=quote.fx_rate,
        status=CashOutStatus.REQUESTED,
        debit_transaction_id=debit.id,
        idempotency_key=idempotency_key,
    )
    db.add(cash_out)
    db.flush()
    logger.info(
        "Cash-out %s requested: %s UCTUSD -> %s %s",
        cash_out.id,
        quote.uctusd_amount,
        quote.payout_amount,
        quote.payout_currency,
    )
    return cash_out


def simulate_cash_out(
    db: Session, cash_out: CashOut, now: datetime | None = None
) -> CashOutStatus:
    """
    Progresses a requested cash-out through approved to completed, and
    credits the fiat leg.

    Both steps happen in one call because the simulated payout rail is
    instant: there is no window in which "approved but not yet paid" is a
    state anyone could observe. The APPROVED timestamp is still recorded,
    so swapping in a real rail means returning after the approval instead
    of falling through to the credit — not restructuring this.
    """
    if cash_out.status != CashOutStatus.REQUESTED:
        raise CashOutStateError(
            f"Cash-out is already {cash_out.status.value}"
        )

    moment = now or _utcnow()
    cash_out.status = CashOutStatus.APPROVED
    cash_out.approved_at = moment

    ledger = Ledger(db)
    user = db.get(User, cash_out.user_id)
    credit = ledger.credit(
        ledger.wallet_for(user),
        cash_out.payout_currency,
        Decimal(cash_out.payout_amount),
    )

    cash_out.credit_transaction_id = credit.id
    cash_out.status = CashOutStatus.COMPLETED
    cash_out.completed_at = moment
    db.flush()
    logger.info("Cash-out %s completed", cash_out.id)
    return cash_out.status


def fail_cash_out(
    db: Session,
    cash_out: CashOut,
    reason: str,
    now: datetime | None = None,
) -> CashOut:
    """
    Rejects a requested cash-out and refunds the reserved UCTUSD.

    The refund is a fresh incoming entry rather than a deletion of the
    debit: wallet_transactions is an immutable audit trail (spec §9.4), so
    a reversal has to be visible as its own line.
    """
    if cash_out.status != CashOutStatus.REQUESTED:
        raise CashOutStateError(
            f"Only a requested cash-out can be failed; this one is "
            f"{cash_out.status.value}"
        )

    ledger = Ledger(db)
    user = db.get(User, cash_out.user_id)
    ledger.credit(
        ledger.wallet_for(user),
        fee_service.SETTLEMENT_CURRENCY,
        Decimal(cash_out.uctusd_amount),
    )

    cash_out.status = CashOutStatus.FAILED
    cash_out.failure_reason = reason[:500]
    cash_out.completed_at = now or _utcnow()
    db.flush()
    logger.warning("Cash-out %s failed: %s", cash_out.id, reason)
    return cash_out


__all__ = [
    "CASH_IN_METHODS",
    "CashInError",
    "CashInStateError",
    "CashOutError",
    "CashOutStateError",
    "InvalidCashInMethodError",
    "InsufficientFundsError",
    "QuoteExpiredError",
    "RecipientNotRegisteredError",
    "SettlementPublishError",
    "assert_recipient_registered",
    "fail_cash_out",
    "payout_currencies",
    "queue_for_settlement",
    "request_cash_out",
    "simulate_cash_in",
    "simulate_cash_out",
]
