"""
The XRPL settlement worker.

Implements the brief's asynchronous settlement flow ("Message Queue")
end to end, in the order the brief specifies:

  1. ZAR payment is confirmed          Track 3, routers/remittances.py
  2. A settlement message is queued    Track 3, SettlementQueue.publish
  3. A worker reads the message        SettlementWorker.run
  4. The UCTUSD transfer is submitted  SettlementWorker.settle, step 3
  5. Success or failure is recorded    SettlementWorker.settle, steps 4/5
  6. The recipient's balance updates   SettlementWorker.settle, step 4

Pooled custody (spec §9.1) decides what step 4 means here. Users have
no XRPL accounts, so the on-chain transfer is not "to the recipient" —
it is a real payment between the platform's two corridor pools (send
pool -> payout pool), submitted once per remittance and hashed onto
that remittance. What the recipient owns is the claim written into the
internal ledger in step 4, now backed by the pool balance that payment
just moved.

That is the same shape a MoneyGram-style corridor has: the customer's
money enters a regional pool, an internal ledger reallocates the
liability, and the payout is made from the destination pool. The
difference is that CrossFX's inter-pool leg is a public XRPL
transaction with a hash, rather than a correspondent-banking wire.

Run separately from the API process:

    python -m worker.settlement_worker

Idempotency (the brief: "must prevent duplicate messages from
crediting the recipient more than once") is enforced three ways, see
spec §9.5:

  - a compare-and-swap on Remittance.status, so only one worker can
    claim a remittance no matter how many times it is delivered
  - a unique constraint on (remittance_id, direction, status) in
    wallet_transactions
  - Remittance.idempotency_key, unique, carried by the message
"""
import enum
import logging
import os
import signal
import socket
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.beneficiary import Beneficiary
from app.models.remittance import Remittance, RemittanceStatus
from app.models.user import User
from app.models.wallet import PlatformWallet, PoolRole
from app.services import fee_service
from app.services.ledger import Direction, EntryStatus, Ledger
from app.services.settlement_queue import SettlementQueue
from app.services.xrpl_service import XRPLService, XRPLTransactionError

logger = logging.getLogger("settlement_worker")

# Statuses a remittance may be claimed from. Track 3's publisher may
# reasonably set either before pushing the message.
CLAIMABLE = (RemittanceStatus.CASH_IN_CONFIRMED, RemittanceStatus.QUEUED)

# ...and the wider set allowed when the message was reclaimed from a
# consumer that stopped responding.
#
# _claim commits SETTLING before the on-chain call. A worker that died in
# that window left the remittance SETTLING forever: SETTLING was not
# claimable, so the redelivery returned SKIPPED — and SKIPPED acks, which
# destroyed the only message pointing at it. Money taken, nothing
# settled, nothing left to retry.
#
# Including SETTLING here is safe precisely because it is gated on the
# reclaim path: Redis only hands the message over after
# settlement_reclaim_idle_ms of silence, which is far longer than a
# healthy settlement takes. A worker that is merely slow still holds its
# message, so it cannot be raced by this.
RECLAIMABLE = CLAIMABLE + (RemittanceStatus.SETTLING,)


class SettlementOutcome(str, enum.Enum):
    """
    What happened to one message. Returned rather than raised so the
    caller, the tests, and Track 4's throughput report can tell the
    three apart.
    """

    SETTLED = "settled"
    FAILED = "failed"
    SKIPPED = "skipped"


class SettlementError(Exception):
    """
    A settlement that cannot be recorded as a plain failure — the
    on-chain payment succeeded but the ledger write did not. Its
    message is deliberately left unacked for reconciliation.
    """


class SettlementWorker:
    """
    Consumes settlement messages and settles them.

    Collaborators are injected so the worker can be driven against a
    fake queue and a fake XRPL node in tests, and so several workers
    can run in one process if a load test ever needs that.
    """

    def __init__(
        self,
        *,
        queue: SettlementQueue | None = None,
        xrpl: XRPLService | None = None,
        session_factory=SessionLocal,
        consumer: str | None = None,
    ) -> None:
        self.queue = queue or SettlementQueue()
        self.xrpl = xrpl or XRPLService()
        self.session_factory = session_factory
        # Distinct per process so several workers can share the group.
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self._stopping = False

    # -- the consume loop -----------------------------------------------

    def run(self) -> None:
        """
        Step 3: read messages and settle them, one at a time.

        The loop is interruptible by SIGINT/SIGTERM, which sets a flag
        so the current message is finished before leaving the loop. The
        worker is idempotent: if it dies mid-message, the message is
        redelivered and the remittance is skipped because it is already
        claimed or settled.
        """
        self._install_signal_handlers()
        self.queue.ensure_consumer_group()
        logger.info(
            "Worker %s consuming %s (group %s)",
            self.consumer,
            self.queue.stream,
            self.queue.group,
        )

        # Anything this consumer was delivered but never acked
        for entry_id, fields in self.queue.read_pending(self.consumer):
            logger.info("Replaying unacked message %s", entry_id)
            self.handle(entry_id, fields, reclaimed=True)

        while not self._stopping:
            # Deliberately broad. This loop used to have no handler at
            # all, so a single redis.ConnectionError — or any database
            # error surfacing from _claim's or _fail's commit — ended the
            # process and stopped settlement for everyone, silently. Log
            # it, wait, and carry on; a worker that cannot reach Redis
            # right now is not a worker that should give up.
            try:
                self._reclaim_abandoned()
                for entry_id, fields in self.queue.read_new(self.consumer):
                    self.handle(entry_id, fields)
            except Exception:
                logger.exception(
                    "Consume loop error — retrying in %ss",
                    settings.settlement_error_backoff_seconds,
                )
                self._sleep(settings.settlement_error_backoff_seconds)

        logger.info("Worker %s stopped", self.consumer)

    def _reclaim_abandoned(self) -> None:
        """
        Takes over anything a dead worker left pending, and settles it.

        Handled here rather than only at startup because the worker that
        died may not be the one that restarts — in a multi-worker
        deployment the survivors are what recovers the casualty's work.
        """
        for entry_id, fields in self.queue.reclaim_stale(
            self.consumer, settings.settlement_reclaim_idle_ms
        ):
            logger.warning(
                "Reclaimed abandoned message %s from another consumer",
                entry_id,
            )
            self.handle(entry_id, fields, reclaimed=True)

    def _sleep(self, seconds: float) -> None:
        """Indirection so tests can drive the loop without real delays."""
        time.sleep(seconds)

    def stop(self) -> None:
        """Finish the current message, then leave the loop."""
        self._stopping = True

    def handle(
        self, entry_id: str, fields: dict, *, reclaimed: bool = False
    ) -> None:
        """Settles one message and acks it if that is the right thing."""
        try:
            outcome = self.settle(fields, reclaimed=reclaimed)
        except SettlementError:
            # on-chain payment landed but the ledger write did not.
            logger.error(
                "Message %s left unacked for reconciliation", entry_id
            )
            return
        self.queue.acknowledge(entry_id)
        logger.debug("Acked %s (%s)", entry_id, outcome.value)

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, signum, _frame) -> None:
        logger.info(
            "Signal %s received, finishing current message then stopping",
            signum,
        )
        self.stop()

    def settle(
        self,
        message: dict,
        db: Session | None = None,
        *,
        reclaimed: bool = False,
    ) -> SettlementOutcome:
        """
        Settles one remittance and returns its outcome.

        Owns its session unless one is injected (the tests inject the
        in-memory one). Never raises for an ordinary settlement
        failure: an exception here would leave the queue message
        unacked and redelivering forever.
        """
        if db is not None:
            return self._settle(message, db, reclaimed=reclaimed)
        session = self.session_factory()
        try:
            return self._settle(message, session, reclaimed=reclaimed)
        finally:
            session.close()

    def _settle(
        self, message: dict, db: Session, *, reclaimed: bool = False
    ) -> SettlementOutcome:
        remittance_id = self._remittance_id(message)
        if remittance_id is None:
            return SettlementOutcome.SKIPPED

        idempotency_key = message.get("idempotency_key")

        # 1. Claim it. Everything past here runs at most once.
        if not self._claim(db, remittance_id, reclaimed=reclaimed):
            logger.info(
                "Skipping %s — already claimed or settled (redelivered)",
                idempotency_key,
            )
            return SettlementOutcome.SKIPPED

        remittance = db.get(Remittance, remittance_id)
        logger.info(
            "Settling %s: %s UCTUSD (idempotency_key=%s)",
            remittance_id,
            remittance.uctusd_amount,
            idempotency_key,
        )

        # 2. Work out who and what, before anything irreversible.
        # recipient is bound before the try so that a failure in either
        # pool lookup still knows who to write the failed entry against.
        # Passing recipient=None unconditionally meant a missing
        # platform_wallets row left the recipient — who was resolved
        # perfectly well — with no record that anything had been tried.
        recipient = None
        try:
            recipient = self._resolve_recipient(db, remittance)
            send_pool = self._load_pool(db, PoolRole.SEND_POOL)
            payout_pool = self._load_pool(db, PoolRole.PAYOUT_POOL)
        except SettlementError as exc:
            return self._fail(db, remittance, str(exc), recipient=recipient)

        amount = Decimal(remittance.uctusd_amount)

        # 3. On-chain leg. The seed is decrypted inside
        #    XRPLService and never reaches this method.
        try:
            tx_hash = self.xrpl.send_pooled_payment(
                send_pool, payout_pool.xrpl_address, str(amount)
            )
        except XRPLTransactionError as exc:
            # Rejected: the ledger is untouched, nothing to undo.
            return self._fail(db, remittance, str(exc), recipient=recipient)
        except Exception as exc:
            # Deliberately broad: network error, timeout, malformed
            # response. An unhandled exception would leave the
            # remittance stuck in SETTLING with its message unacked, so
            # recording a failure keeps FAILED as the one retry surface.
            logger.exception(
                "Unexpected error submitting payment for %s", remittance_id
            )
            reason = f"{type(exc).__name__}: {exc}"
            return self._fail(db, remittance, reason, recipient=recipient)

        logger.info("Payment for %s settled: %s", remittance_id, tx_hash)

        # 4. Ledger leg plus the remittance stamp in one transaction.
        return self._record_success(
            db, remittance, recipient, amount, tx_hash
        )

    def _record_success(
        self,
        db: Session,
        remittance: Remittance,
        recipient: User,
        amount: Decimal,
        tx_hash: str,
    ) -> SettlementOutcome:
        ledger = Ledger(db)
        try:
            ledger.credit(
                ledger.wallet_for(recipient),
                fee_service.SETTLEMENT_CURRENCY,
                amount,
                remittance_id=remittance.id,
                xrpl_tx_hash=tx_hash,
            )

            # The sender's side is a record, not a balance move
            sender = db.get(User, remittance.sender_id)
            if sender is not None:
                ledger.record_external(
                    ledger.wallet_for(sender),
                    Direction.OUTGOING,
                    fee_service.SEND_CURRENCY,
                    Decimal(remittance.zar_send_amount),
                    remittance_id=remittance.id,
                    xrpl_tx_hash=tx_hash,
                )

            remittance.status = RemittanceStatus.SETTLED
            remittance.xrpl_tx_hash = tx_hash
            remittance.settled_at = datetime.now(timezone.utc)
            # The on-chain leg is this remittance's treasury
            # The batch id is 1:1 with the payment today;
            # the column stays because netting several remittances into
            # one payment is the production shape (spec §9.6).
            remittance.treasury_batch_id = uuid.uuid4().hex
            remittance.treasury_settled_at = remittance.settled_at
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.exception(
                "Payment %s succeeded but the ledger write failed for %s — "
                "needs reconciliation, left in SETTLING",
                tx_hash,
                remittance.id,
            )
            raise SettlementError(
                f"ledger write failed after on-chain success: {exc}"
            ) from exc

        logger.info(
            "Settled %s -> %s UCTUSD credited to %s",
            remittance.id,
            amount,
            recipient.email,
        )
        return SettlementOutcome.SETTLED

    def _fail(
        self,
        db: Session,
        remittance: Remittance,
        reason: str,
        *,
        recipient: User | None,
    ) -> SettlementOutcome:
        """
        Records a failed settlement: no credit, a visible failed entry
        on the recipient's wallet when we know who they are, and
        FAILED on the remittance.

        `reason` is an XRPL result code or an internal message.
        """
        db.rollback()
        remittance = db.get(Remittance, remittance.id)
        remittance.status = RemittanceStatus.FAILED

        if recipient is not None:
            ledger = Ledger(db)
            ledger.record_external(
                ledger.wallet_for(recipient),
                Direction.INCOMING,
                fee_service.SETTLEMENT_CURRENCY,
                Decimal(remittance.uctusd_amount),
                remittance_id=remittance.id,
                status=EntryStatus.FAILED,
                failure_reason=reason[:500],
            )

        try:
            db.commit()
        except IntegrityError:
            # uq_wallet_tx_remittance_direction_status already holds a
            # failed incoming entry for this remittance — this is its
            # second failure, after an admin retried it. The entry is a
            # record that an attempt failed, and one is enough; what
            # must not happen is this raising, because _fail is the
            # worker's own error handler and an exception here escaped
            # all the way out of run() and killed the process.
            db.rollback()
            remittance = db.get(Remittance, remittance.id)
            remittance.status = RemittanceStatus.FAILED
            db.commit()
            logger.warning(
                "Settlement for %s failed again; the existing failed entry "
                "stands",
                remittance.id,
            )

        logger.error("Settlement failed for %s: %s", remittance.id, reason)
        return SettlementOutcome.FAILED

    # lookups

    @staticmethod
    def _remittance_id(message: dict) -> uuid.UUID | None:
        """Parses the message, or None if it is unusable."""
        if not message.get("idempotency_key") or not message.get(
            "remittance_id"
        ):
            logger.error(
                "Malformed settlement message, dropping: keys=%s",
                sorted(message),
            )
            return None
        try:
            return uuid.UUID(str(message["remittance_id"]))
        except ValueError:
            logger.error("Message has a non-UUID remittance_id, dropping")
            return None

    @staticmethod
    def _claim(
        db: Session, remittance_id: uuid.UUID, *, reclaimed: bool = False
    ) -> bool:
        """
        Compare-and-swap: move the remittance to SETTLING only if it is
        currently awaiting settlement. Returns False if another worker
        already claimed it, or if it has already settled.

        `reclaimed` widens the accepted set to include SETTLING. It is
        set only for a message Redis handed over after the previous
        consumer went quiet for settlement_reclaim_idle_ms — i.e. one
        whose worker is gone. Without it a remittance stranded mid-settle
        could never be picked up again, and the redelivery acked the
        message away.
        """
        claimable = RECLAIMABLE if reclaimed else CLAIMABLE
        claimed = (
            db.query(Remittance)
            .filter(
                Remittance.id == remittance_id,
                Remittance.status.in_(claimable),
            )
            .update(
                {Remittance.status: RemittanceStatus.SETTLING},
                synchronize_session=False,
            )
        )
        db.commit()
        return claimed == 1

    @staticmethod
    def _load_pool(db: Session, role: PoolRole) -> PlatformWallet:
        """
        Loaded per message rather than cached, so rotating a pool
        wallet does not need a worker restart.
        """
        pool = (
            db.query(PlatformWallet)
            .filter(PlatformWallet.role == role)
            .first()
        )
        if pool is None:
            raise SettlementError(
                f"No {role.value} PlatformWallet row exists. Run: "
                "python -m scripts.init_platform_wallets"
            )
        return pool

    @staticmethod
    def _resolve_recipient(db: Session, remittance: Remittance) -> User:
        """
        Finds the platform user whose wallet the claim belongs to.

        A Remittance points at a Beneficiary, which is contact details
        rather than an account — so the recipient is matched by
        Beneficiary.contact against User.email, case-insensitively.
        That is a known sharp edge: a beneficiary who has not registered
        cannot be credited. Matching without regard to case removes the
        half of it that was merely a bug — "Alice@x.com" and
        "alice@x.com" are one person — and BeneficiaryCreate now
        validates the field as an email and stores it lowercase, so a
        phone number can no longer be saved into a field only an email
        lookup can resolve.
        """
        beneficiary = db.get(Beneficiary, remittance.beneficiary_id)
        if beneficiary is None:
            raise SettlementError(
                f"Remittance {remittance.id} has no beneficiary row"
            )

        recipient = (
            db.query(User)
            .filter(
                func.lower(User.email) == beneficiary.contact.strip().lower()
            )
            .first()
        )
        if recipient is None:
            raise SettlementError(
                f"No registered user matches the beneficiary contact for "
                f"remittance {remittance.id} — the recipient must register "
                f"before settlement"
            )
        return recipient


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    SettlementWorker().run()


if __name__ == "__main__":
    main()
