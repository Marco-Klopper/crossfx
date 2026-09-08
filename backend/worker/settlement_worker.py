"""
The XRPL settlement worker.

Implements the brief's asynchronous settlement flow ("Message Queue")
end to end, in the order the brief specifies:

  1. ZAR payment is confirmed          Track 3, routers/remittances.py
  2. A settlement message is queued    Track 3, SettlementQueue.publish
  3. A worker reads the message        SettlementWorker.run
  4. The RLUSD transfer is submitted   SettlementWorker.settle, step 3
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
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.beneficiary import Beneficiary
from app.models.remittance import Remittance, RemittanceStatus
from app.models.user import User
from app.models.wallet import PlatformWallet, PoolRole
from app.services.ledger import Direction, EntryStatus, Ledger
from app.services.settlement_queue import SettlementQueue
from app.services.xrpl_service import XRPLService, XRPLTransactionError

logger = logging.getLogger("settlement_worker")

# Statuses a remittance may be claimed from. Track 3's publisher may
# reasonably set either before pushing the message.
CLAIMABLE = (RemittanceStatus.CASH_IN_CONFIRMED, RemittanceStatus.QUEUED)


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
            self.handle(entry_id, fields)

        while not self._stopping:
            for entry_id, fields in self.queue.read_new(self.consumer):
                self.handle(entry_id, fields)

        logger.info("Worker %s stopped", self.consumer)

    def stop(self) -> None:
        """Finish the current message, then leave the loop."""
        self._stopping = True

    def handle(self, entry_id: str, fields: dict) -> None:
        """Settles one message and acks it if that is the right thing."""
        try:
            outcome = self.settle(fields)
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
        self, message: dict, db: Session | None = None
    ) -> SettlementOutcome:
        """
        Settles one remittance and returns its outcome.

        Owns its session unless one is injected (the tests inject the
        in-memory one). Never raises for an ordinary settlement
        failure: an exception here would leave the queue message
        unacked and redelivering forever.
        """
        if db is not None:
            return self._settle(message, db)
        session = self.session_factory()
        try:
            return self._settle(message, session)
        finally:
            session.close()

    def _settle(self, message: dict, db: Session) -> SettlementOutcome:
        remittance_id = self._remittance_id(message)
        if remittance_id is None:
            return SettlementOutcome.SKIPPED

        idempotency_key = message.get("idempotency_key")

        # 1. Claim it. Everything past here runs at most once.
        if not self._claim(db, remittance_id):
            logger.info(
                "Skipping %s — already claimed or settled (redelivered)",
                idempotency_key,
            )
            return SettlementOutcome.SKIPPED

        remittance = db.get(Remittance, remittance_id)
        logger.info(
            "Settling %s: %s RLUSD (idempotency_key=%s)",
            remittance_id,
            remittance.rlusd_amount,
            idempotency_key,
        )

        # 2. Work out who and what, before anything irreversible.
        try:
            recipient = self._resolve_recipient(db, remittance)
            send_pool = self._load_pool(db, PoolRole.SEND_POOL)
            payout_pool = self._load_pool(db, PoolRole.PAYOUT_POOL)
        except SettlementError as exc:
            return self._fail(db, remittance, str(exc), recipient=None)

        amount = Decimal(remittance.rlusd_amount)

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
                "RLUSD",
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
                    "ZAR",
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
            "Settled %s -> %s RLUSD credited to %s",
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
                "RLUSD",
                Decimal(remittance.rlusd_amount),
                remittance_id=remittance.id,
                status=EntryStatus.FAILED,
                failure_reason=reason[:500],
            )

        db.commit()
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
    def _claim(db: Session, remittance_id: uuid.UUID) -> bool:
        """
        Compare-and-swap: move the remittance to SETTLING only if it is
        currently awaiting settlement. Returns False if another worker
        already claimed it, or if it has already settled.
        """
        claimed = (
            db.query(Remittance)
            .filter(
                Remittance.id == remittance_id,
                Remittance.status.in_(CLAIMABLE),
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
        Beneficiary.contact against User.email. That is a known sharp
        edge: a beneficiary who has not registered cannot be credited,
        and one who registered under a different address than the
        sender typed will not be found. 
        """
        beneficiary = db.get(Beneficiary, remittance.beneficiary_id)
        if beneficiary is None:
            raise SettlementError(
                f"Remittance {remittance.id} has no beneficiary row"
            )

        recipient = (
            db.query(User).filter(User.email == beneficiary.contact).first()
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
