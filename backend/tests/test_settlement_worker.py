"""
worker.settlement_worker.SettlementWorker — the brief's mandated
asynchronous settlement flow, in full.

The XRPL service and the queue are injected as fakes, so no live
Testnet and no broker are needed and almost nothing is patched.

The properties under test are the ones the brief and spec §9 promise:
  - a real payment is submitted per remittance, send pool -> payout pool
  - success and failure are both recorded, never raised past the worker
  - the recipient's ledger balance moves only on success
  - a redelivered message can never credit twice
  - a wallet seed never leaves XRPLService
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.remittance import RemittanceStatus
from app.models.wallet import WalletTransaction
from app.services.ledger import Ledger
from app.services.xrpl_service import XRPLTransactionError
from worker.settlement_worker import (
    SettlementError,
    SettlementOutcome,
    SettlementWorker,
)


@pytest.fixture()
def xrpl():
    """A fake XRPLService whose payments always succeed."""
    service = MagicMock()
    service.send_pooled_payment.return_value = "TXHASH123"
    return service


@pytest.fixture()
def queue():
    return MagicMock()


@pytest.fixture()
def worker(xrpl, queue):
    return SettlementWorker(queue=queue, xrpl=xrpl, consumer="test-worker")


@pytest.fixture()
def ledger(db_session):
    return Ledger(db_session)


def message_for(remittance):
    return {
        "idempotency_key": remittance.idempotency_key,
        "remittance_id": str(remittance.id),
    }


class TestHappyPath:
    def test_submits_one_payment_send_pool_to_payout_pool(
        self, worker, xrpl, db_session, pool_wallets, remittance_factory
    ):
        send_pool, payout_pool = pool_wallets
        remittance, _sender, _recipient = remittance_factory(
            rlusd_amount=Decimal("52.5")
        )

        outcome = worker.settle(message_for(remittance), db_session)

        assert outcome is SettlementOutcome.SETTLED
        xrpl.send_pooled_payment.assert_called_once()
        pool, destination, amount = xrpl.send_pooled_payment.call_args[0]
        assert pool.id == send_pool.id
        assert destination == payout_pool.xrpl_address
        assert Decimal(amount) == Decimal("52.5")

    def test_credits_the_recipients_rlusd_claim(
        self, worker, db_session, ledger, pool_wallets, remittance_factory
    ):
        remittance, _sender, recipient = remittance_factory(
            rlusd_amount=Decimal("52.5")
        )

        worker.settle(message_for(remittance), db_session)

        wallet = ledger.wallet_for(recipient)
        assert ledger.balance(wallet, "RLUSD") == Decimal("52.5")

    def test_stamps_hash_status_and_treasury_fields(
        self, worker, db_session, pool_wallets, remittance_factory
    ):
        remittance, _sender, _recipient = remittance_factory()

        worker.settle(message_for(remittance), db_session)
        db_session.refresh(remittance)

        assert remittance.status == RemittanceStatus.SETTLED
        assert remittance.xrpl_tx_hash == "TXHASH123"
        assert remittance.settled_at is not None
        # The on-chain leg IS this remittance's treasury settlement, so
        # both treasury columns are filled by the worker rather than by
        # a later batch job.
        assert remittance.treasury_batch_id is not None
        assert remittance.treasury_settled_at is not None

    def test_records_senders_outgoing_zar_without_debiting_them(
        self, worker, db_session, ledger, pool_wallets, remittance_factory
    ):
        remittance, sender, _recipient = remittance_factory(
            zar_send_amount=Decimal("1000.00")
        )

        worker.settle(message_for(remittance), db_session)

        wallet = ledger.wallet_for(sender)
        entry = (
            db_session.query(WalletTransaction)
            .filter(WalletTransaction.wallet_id == wallet.id)
            .one()
        )
        assert entry.direction == "outgoing"
        assert entry.currency == "ZAR"
        assert Decimal(entry.amount) == Decimal("1000.00")
        # Their ZAR went bank -> pool; never a holding of theirs.
        assert ledger.balance(wallet, "ZAR") == Decimal("0")

    def test_recipient_entry_carries_the_xrpl_hash(
        self, worker, db_session, ledger, pool_wallets, remittance_factory
    ):
        remittance, _sender, recipient = remittance_factory()

        worker.settle(message_for(remittance), db_session)

        wallet = ledger.wallet_for(recipient)
        entry = (
            db_session.query(WalletTransaction)
            .filter(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.direction == "incoming",
            )
            .one()
        )
        assert entry.xrpl_tx_hash == "TXHASH123"
        assert entry.status == "success"

    def test_accepts_a_remittance_already_marked_queued(
        self, worker, db_session, pool_wallets, remittance_factory
    ):
        """Track 3 may set CASH_IN_CONFIRMED or QUEUED before publishing."""
        remittance, _sender, _recipient = remittance_factory(
            status=RemittanceStatus.QUEUED
        )

        outcome = worker.settle(message_for(remittance), db_session)
        assert outcome is SettlementOutcome.SETTLED


class TestIdempotency:
    """The brief: duplicates must not credit the recipient twice."""

    def test_a_redelivered_message_is_skipped(
        self, worker, xrpl, db_session, ledger, pool_wallets,
        remittance_factory,
    ):
        remittance, _sender, recipient = remittance_factory(
            rlusd_amount=Decimal("52.5")
        )
        message = message_for(remittance)

        first = worker.settle(message, db_session)
        second = worker.settle(message, db_session)
        third = worker.settle(message, db_session)

        assert first is SettlementOutcome.SETTLED
        assert second is third is SettlementOutcome.SKIPPED
        # One payment, one credit — not three.
        assert xrpl.send_pooled_payment.call_count == 1
        wallet = ledger.wallet_for(recipient)
        assert ledger.balance(wallet, "RLUSD") == Decimal("52.5")

    @pytest.mark.parametrize(
        "status", [RemittanceStatus.QUOTED, RemittanceStatus.SETTLED]
    )
    def test_an_unclaimable_remittance_is_not_settled(
        self, worker, xrpl, db_session, pool_wallets, remittance_factory,
        status,
    ):
        """
        QUOTED means cash-in was never confirmed, which the brief
        forbids settling. SETTLED means it is already done.
        """
        remittance, _sender, _recipient = remittance_factory(status=status)

        outcome = worker.settle(message_for(remittance), db_session)

        assert outcome is SettlementOutcome.SKIPPED
        xrpl.send_pooled_payment.assert_not_called()


class TestFailureHandling:
    """The brief's "failed-transaction handling"; recorded, not raised."""

    def test_a_rejected_payment_fails_and_credits_nothing(
        self, worker, xrpl, db_session, ledger, pool_wallets,
        remittance_factory,
    ):
        remittance, _sender, recipient = remittance_factory()
        xrpl.send_pooled_payment.side_effect = XRPLTransactionError(
            "XRPL transaction failed: tecUNFUNDED_PAYMENT"
        )

        outcome = worker.settle(message_for(remittance), db_session)

        assert outcome is SettlementOutcome.FAILED
        db_session.refresh(remittance)
        assert remittance.status == RemittanceStatus.FAILED
        assert remittance.xrpl_tx_hash is None
        assert ledger.balance(
            ledger.wallet_for(recipient), "RLUSD"
        ) == Decimal("0")

    def test_a_failure_is_still_visible_to_the_recipient(
        self, worker, xrpl, db_session, ledger, pool_wallets,
        remittance_factory,
    ):
        remittance, _sender, recipient = remittance_factory()
        xrpl.send_pooled_payment.side_effect = XRPLTransactionError(
            "XRPL transaction failed: tecPATH_DRY", result_code="tecPATH_DRY"
        )

        worker.settle(message_for(remittance), db_session)

        wallet = ledger.wallet_for(recipient)
        entry = (
            db_session.query(WalletTransaction)
            .filter(WalletTransaction.wallet_id == wallet.id)
            .one()
        )
        assert entry.status == "failed"
        assert "tecPATH_DRY" in entry.failure_reason

    def test_a_network_error_is_recorded_not_raised(
        self, worker, xrpl, db_session, pool_wallets, remittance_factory
    ):
        """
        An escaping exception would leave the remittance stuck in
        SETTLING with its message unacked, redelivering forever.
        """
        remittance, _sender, _recipient = remittance_factory()
        xrpl.send_pooled_payment.side_effect = TimeoutError(
            "connection to rippletest.net timed out"
        )

        outcome = worker.settle(message_for(remittance), db_session)

        assert outcome is SettlementOutcome.FAILED
        db_session.refresh(remittance)
        assert remittance.status == RemittanceStatus.FAILED

    def test_missing_pool_wallets_fails_cleanly(
        self, worker, xrpl, db_session, remittance_factory
    ):
        """No pool_wallets fixture — platform_wallets is empty."""
        remittance, _sender, _recipient = remittance_factory()

        outcome = worker.settle(message_for(remittance), db_session)

        assert outcome is SettlementOutcome.FAILED
        xrpl.send_pooled_payment.assert_not_called()
        db_session.refresh(remittance)
        assert remittance.status == RemittanceStatus.FAILED

    def test_an_unregistered_recipient_fails_cleanly(
        self, worker, xrpl, db_session, pool_wallets, remittance_factory
    ):
        """
        A beneficiary who never registered has no wallet to credit.
        Documented as a limitation in spec §15.
        """
        remittance, _sender, _recipient = remittance_factory(
            register_recipient=False
        )

        outcome = worker.settle(message_for(remittance), db_session)

        assert outcome is SettlementOutcome.FAILED
        xrpl.send_pooled_payment.assert_not_called()


class TestMalformedMessages:
    @pytest.mark.parametrize(
        "message",
        [
            {},
            {"idempotency_key": "k"},
            {"remittance_id": "00000000-0000-0000-0000-000000000000"},
            {"idempotency_key": "k", "remittance_id": "not-a-uuid"},
        ],
    )
    def test_dropped_without_raising(
        self, worker, xrpl, db_session, pool_wallets, message
    ):
        outcome = worker.settle(message, db_session)

        assert outcome is SettlementOutcome.SKIPPED
        xrpl.send_pooled_payment.assert_not_called()


class TestKeyMaterialBoundary:
    def test_the_worker_passes_a_pool_row_never_a_seed(
        self, worker, xrpl, db_session, pool_wallets, remittance_factory
    ):
        """
        The brief requires seeds to be decrypted only by the signing
        component, so the worker must hand XRPLService a PlatformWallet
        row and never a seed string.
        """
        remittance, _sender, _recipient = remittance_factory()

        worker.settle(message_for(remittance), db_session)

        args = xrpl.send_pooled_payment.call_args[0]
        assert hasattr(args[0], "xrpl_encrypted_seed")
        assert not any(
            isinstance(arg, str) and arg.startswith("sEd") for arg in args
        )


class TestAckBehaviour:
    """
    The ack is the only thing between a crash and a lost remittance, so
    when it does and does not happen is worth pinning down.
    """

    def test_acked_only_after_settling_returns(self, worker, queue):
        calls = []
        worker.settle = lambda fields: calls.append("settled") or (
            SettlementOutcome.SETTLED
        )
        queue.acknowledge.side_effect = lambda entry_id: calls.append(
            f"acked:{entry_id}"
        )

        worker.handle("1700000000-0", {"idempotency_key": "k"})

        assert calls == ["settled", "acked:1700000000-0"]

    def test_a_failed_settlement_is_still_acked(self, worker, queue):
        """
        A recorded failure is a completed outcome — redelivering would
        just re-fail. Only the reconciliation case stays pending.
        """
        worker.settle = lambda fields: SettlementOutcome.FAILED

        worker.handle("1700000000-1", {})

        queue.acknowledge.assert_called_once_with("1700000000-1")

    def test_the_reconciliation_case_is_left_unacked(self, worker, queue):
        """
        On-chain payment succeeded, ledger write did not. The message
        must stay pending so the discrepancy cannot be quietly
        forgotten (spec §9.5).
        """

        def boom(fields):
            raise SettlementError("ledger write failed after on-chain success")

        worker.settle = boom

        worker.handle("1700000000-2", {})

        queue.acknowledge.assert_not_called()


class TestRunLoop:
    def test_drains_pending_then_new_messages_then_stops(
        self, worker, queue
    ):
        """
        Unacked messages from a previous life are replayed before new
        work is taken.
        """
        handled = []
        worker.settle = lambda fields: handled.append(
            fields["idempotency_key"]
        ) or SettlementOutcome.SETTLED

        queue.read_pending.return_value = [
            ("1-0", {"idempotency_key": "replayed"})
        ]

        def read_new(consumer, count=1):
            worker.stop()
            return [("2-0", {"idempotency_key": "fresh"})]

        queue.read_new.side_effect = read_new

        worker.run()

        assert handled == ["replayed", "fresh"]
        queue.ensure_consumer_group.assert_called_once()
