"""
Integration tests for the cash-out flow (spec §10): request, admin
approval or rejection, and the ledger movements behind both.

The shape being pinned is that the UCTUSD leaves the balance when the
cash-out is *requested*, not when it is approved — otherwise one balance
could back three approved payouts.
"""
import uuid
from decimal import Decimal

import pytest

from app.config import settings
from app.models.remittance import CashOut, CashOutStatus
from app.schemas.remittance import CashOutRead
from app.services.ledger import Ledger
from app.services.settlement_queue import SettlementQueue
from app.services.xrpl_service import XRPLService, XRPLTransactionError
from unittest.mock import create_autospec
from worker.settlement_worker import SettlementOutcome, SettlementWorker


@pytest.fixture(autouse=True)
def stable_rate(monkeypatch):
    monkeypatch.setattr(settings, "exchange_rate_source", "mock")
    monkeypatch.setattr(settings, "fx_mock_base_rate", 18.50)
    monkeypatch.setattr(settings, "fx_mock_volatility_bps", 0)
    monkeypatch.setattr(settings, "cashout_fee_bps", 100)


@pytest.fixture(autouse=True)
def queue(fake_queue):
    """Approving publishes a burn message; no Redis is needed to test it."""
    return fake_queue


@pytest.fixture()
def xrpl():
    service = create_autospec(XRPLService, instance=True)
    service.burn.return_value = "BURNTX123"
    return service


@pytest.fixture()
def worker(xrpl, pool_wallets):
    return SettlementWorker(
        queue=create_autospec(SettlementQueue, instance=True),
        xrpl=xrpl,
        consumer="test-worker",
    )


def run_burn(worker, db_session, cash_out_id):
    """Delivers the burn message the approval published."""
    return worker.burn_cash_out(
        {"kind": "cash_out", "cash_out_id": str(cash_out_id)}, db_session
    )


def approve_and_burn(client, admin, cash_out_id, worker, db_session):
    response = client.post(
        f"/admin/cash-outs/{cash_out_id}/approve", headers=admin
    )
    assert response.status_code == 200
    run_burn(worker, db_session, cash_out_id)
    return response


@pytest.fixture()
def funded_recipient(db_session, user_factory, auth_header_for):
    """A recipient holding 100 UCTUSD, as a settlement would have left them."""
    user = user_factory(email="recipient@example.com")
    ledger = Ledger(db_session)
    ledger.credit(ledger.wallet_for(user), "UCTUSD", Decimal("100.000000"))
    db_session.commit()
    return auth_header_for(user), user


def _balance(db_session, user, currency):
    ledger = Ledger(db_session)
    return ledger.balance(ledger.wallet_for(user), currency)


class TestAuth:
    def test_requesting_requires_a_token(self, client):
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("path", ["/wallet/cash-outs", "/admin/cash-outs"])
    def test_listing_requires_a_token(self, client, path):
        assert client.get(path).status_code == 401

    def test_approving_requires_an_admin(
        self, client, funded_recipient, db_session
    ):
        headers, _ = funded_recipient
        cash_out_id = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        ).json()["id"]

        response = client.post(
            f"/admin/cash-outs/{cash_out_id}/approve", headers=headers
        )
        assert response.status_code == 403


class TestRequest:
    def test_reserves_the_token_immediately(
        self, client, funded_recipient, db_session
    ):
        headers, user = funded_recipient

        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "50", "payout_currency": "USD"},
            headers=headers,
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "requested"
        assert Decimal(body["cash_out_fee_uctusd"]) == Decimal("0.500000")
        assert Decimal(body["net_uctusd"]) == Decimal("49.500000")
        assert Decimal(body["payout_amount"]) == Decimal("49.50")

        db_session.expire_all()
        assert _balance(db_session, user, "UCTUSD") == Decimal("50.000000")
        # The fiat leg has not happened yet — approval does that.
        assert _balance(db_session, user, "USD") == Decimal("0")

    def test_converts_to_rand_at_the_current_rate(
        self, client, funded_recipient
    ):
        headers, _ = funded_recipient

        body = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "50", "payout_currency": "ZAR"},
            headers=headers,
        ).json()

        assert body["payout_currency"] == "ZAR"
        assert Decimal(body["payout_amount"]) == Decimal("915.75")

    def test_refuses_more_than_the_balance(self, client, funded_recipient):
        """
        Ledger.debit refusing to go negative is the brief's "validate
        sufficient balance" step — there is no second check to get wrong.
        """
        headers, _ = funded_recipient

        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "500", "payout_currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 400
        assert "cannot debit" in response.json()["detail"]

    def test_a_refused_request_leaves_the_balance_untouched(
        self, client, funded_recipient, db_session
    ):
        headers, user = funded_recipient
        client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "500", "payout_currency": "USD"},
            headers=headers,
        )

        db_session.expire_all()
        assert _balance(db_session, user, "UCTUSD") == Decimal("100.000000")
        assert db_session.query(CashOut).count() == 0

    def test_two_requests_cannot_share_one_balance(
        self, client, funded_recipient, db_session
    ):
        """The reason the debit happens on request rather than on approval."""
        headers, user = funded_recipient
        payload = {"uctusd_amount": "60", "payout_currency": "USD"}

        first = client.post("/wallet/cash-out", json=payload, headers=headers)
        second = client.post("/wallet/cash-out", json=payload, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 400
        db_session.expire_all()
        assert _balance(db_session, user, "UCTUSD") == Decimal("40.000000")

    @pytest.mark.parametrize("currency", ["EUR", "UCTUSD", "wat"])
    def test_rejects_a_currency_the_corridor_cannot_pay(
        self, client, funded_recipient, currency
    ):
        headers, _ = funded_recipient
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": currency},
            headers=headers,
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("amount", ["0", "-5"])
    def test_rejects_a_non_positive_amount(
        self, client, funded_recipient, amount
    ):
        headers, _ = funded_recipient
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": amount, "payout_currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 422


class TestApproval:
    @pytest.fixture()
    def requested(self, client, funded_recipient):
        headers, user = funded_recipient
        body = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "50", "payout_currency": "USD"},
            headers=headers,
        ).json()
        return body, headers, user

    def test_approving_queues_the_burn_and_pays_nothing_yet(
        self, client, requested, admin_headers, db_session, queue
    ):
        body, _, user = requested
        admin, _ = admin_headers

        response = client.post(
            f"/admin/cash-outs/{body['id']}/approve", headers=admin
        )

        assert response.status_code == 200
        approved = response.json()
        assert approved["status"] == "approved"
        assert approved["approved_at"] is not None
        assert approved["completed_at"] is None
        assert queue.cash_outs_published == [body["id"]]

        db_session.expire_all()
        assert _balance(db_session, user, "USD") == Decimal("0")

    def test_the_worker_burns_then_credits_the_fiat_leg(
        self, client, requested, admin_headers, db_session, worker, xrpl,
        pool_wallets,
    ):
        body, headers, user = requested
        admin, _ = admin_headers
        _send_pool, payout_pool = pool_wallets
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

        outcome = run_burn(worker, db_session, body["id"])

        assert outcome == SettlementOutcome.SETTLED
        # The NET amount is burned, from the payout pool; the fee stays.
        xrpl.burn.assert_called_once()
        pool_arg, amount_arg = xrpl.burn.call_args.args
        assert pool_arg.id == payout_pool.id
        assert Decimal(amount_arg) == Decimal("49.500000")

        done = client.get(
            f"/wallet/cash-outs/{body['id']}", headers=headers
        ).json()
        assert done["status"] == "completed"
        assert done["xrpl_tx_hash"] == "BURNTX123"
        assert done["completed_at"] is not None

        db_session.expire_all()
        assert _balance(db_session, user, "USD") == Decimal("49.500000")
        assert _balance(db_session, user, "UCTUSD") == Decimal("50.000000")

    def test_a_redelivered_message_burns_only_once(
        self, client, requested, admin_headers, db_session, worker, xrpl
    ):
        body, _, user = requested
        admin, _ = admin_headers
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

        first = run_burn(worker, db_session, body["id"])
        second = run_burn(worker, db_session, body["id"])

        assert first == SettlementOutcome.SETTLED
        assert second == SettlementOutcome.SKIPPED
        assert xrpl.burn.call_count == 1
        db_session.expire_all()
        assert _balance(db_session, user, "USD") == Decimal("49.500000")

    def test_a_rejected_burn_refunds_the_token_and_pays_no_fiat(
        self, client, requested, admin_headers, db_session, worker, xrpl
    ):
        body, headers, user = requested
        admin, _ = admin_headers
        xrpl.burn.side_effect = XRPLTransactionError(
            "XRPL transaction failed: tecPATH_DRY", result_code="tecPATH_DRY"
        )
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

        outcome = run_burn(worker, db_session, body["id"])

        assert outcome == SettlementOutcome.FAILED
        failed = client.get(
            f"/wallet/cash-outs/{body['id']}", headers=headers
        ).json()
        assert failed["status"] == "failed"
        assert "tecPATH_DRY" in failed["failure_reason"]
        assert failed["xrpl_tx_hash"] is None
        db_session.expire_all()
        assert _balance(db_session, user, "UCTUSD") == Decimal("100.000000")
        assert _balance(db_session, user, "USD") == Decimal("0")

    def test_a_missing_payout_pool_fails_the_cash_out_and_refunds(
        self, client, requested, admin_headers, db_session, xrpl
    ):
        """No pool_wallets fixture here: the payout pool row is absent."""
        body, _, user = requested
        admin, _ = admin_headers
        bare_worker = SettlementWorker(
            queue=create_autospec(SettlementQueue, instance=True),
            xrpl=xrpl,
            consumer="t",
        )
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

        outcome = run_burn(bare_worker, db_session, body["id"])

        assert outcome == SettlementOutcome.FAILED
        xrpl.burn.assert_not_called()
        db_session.expire_all()
        assert _balance(db_session, user, "UCTUSD") == Decimal("100.000000")

    def test_approving_again_republishes_when_the_queue_was_down(
        self, client, requested, admin_headers, queue
    ):
        body, _, _ = requested
        admin, _ = admin_headers
        queue.fail_with = ConnectionError("Connection refused")

        down = client.post(
            f"/admin/cash-outs/{body['id']}/approve", headers=admin
        )
        assert down.status_code == 503

        queue.fail_with = None
        retry = client.post(
            f"/admin/cash-outs/{body['id']}/approve", headers=admin
        )
        assert retry.status_code == 200
        assert retry.json()["status"] == "approved"
        assert queue.cash_outs_published == [body["id"]]

    def test_the_fee_stays_with_the_platform(
        self, client, requested, admin_headers, db_session, worker
    ):
        """
        50 UCTUSD left the recipient, 49.50 came back as USD. The 0.50
        difference is the cash-out fee, and it is not credited anywhere —
        it is the platform's revenue (spec §4).
        """
        body, _, user = requested
        admin, _ = admin_headers
        approve_and_burn(client, admin, body["id"], worker, db_session)

        db_session.expire_all()
        assert (
            _balance(db_session, user, "UCTUSD")
            + Decimal(body["net_uctusd"])
            + Decimal(body["cash_out_fee_uctusd"])
            == Decimal("100.000000")
        )

    def test_both_legs_appear_in_the_wallet_history(
        self, client, requested, admin_headers, worker, db_session
    ):
        body, headers, _ = requested
        admin, _ = admin_headers
        approve_and_burn(client, admin, body["id"], worker, db_session)

        history = client.get("/wallet/transactions", headers=headers).json()
        directions = {(t["direction"], t["currency"]) for t in history}
        assert ("outgoing", "UCTUSD") in directions
        assert ("incoming", "USD") in directions

    def test_cannot_be_approved_once_completed(
        self, client, requested, admin_headers, db_session, worker
    ):
        body, _, user = requested
        admin, _ = admin_headers
        approve_and_burn(client, admin, body["id"], worker, db_session)

        second = client.post(
            f"/admin/cash-outs/{body['id']}/approve", headers=admin
        )
        assert second.status_code == 409

        db_session.expire_all()
        assert _balance(db_session, user, "USD") == Decimal("49.500000")

    def test_unknown_id_is_404(self, client, admin_headers):
        admin, _ = admin_headers
        assert (
            client.post(
                f"/admin/cash-outs/{uuid.uuid4()}/approve", headers=admin
            ).status_code
            == 404
        )


class TestRejection:
    @pytest.fixture()
    def requested(self, client, funded_recipient):
        headers, user = funded_recipient
        body = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "50", "payout_currency": "USD"},
            headers=headers,
        ).json()
        return body, headers, user

    def test_refunds_the_reserved_token(
        self, client, requested, admin_headers, db_session
    ):
        body, _, user = requested
        admin, _ = admin_headers

        response = client.post(
            f"/admin/cash-outs/{body['id']}/reject",
            json={"reason": "Payout partner declined"},
            headers=admin,
        )

        assert response.status_code == 200
        rejected = response.json()
        assert rejected["status"] == "failed"
        assert rejected["failure_reason"] == "Payout partner declined"

        db_session.expire_all()
        assert _balance(db_session, user, "UCTUSD") == Decimal("100.000000")
        assert _balance(db_session, user, "USD") == Decimal("0")

    def test_the_refund_is_a_visible_entry_not_an_undo(
        self, client, requested, admin_headers
    ):
        """wallet_transactions is an immutable audit trail (spec §9.4)."""
        body, headers, _ = requested
        admin, _ = admin_headers
        client.post(
            f"/admin/cash-outs/{body['id']}/reject",
            json={"reason": "Payout partner declined"},
            headers=admin,
        )

        history = client.get("/wallet/transactions", headers=headers).json()
        uctusd = [t for t in history if t["currency"] == "UCTUSD"]
        # The original credit, the cash-out debit, and the refund.
        assert len(uctusd) == 3
        assert [t["direction"] for t in uctusd] == [
            "incoming",
            "outgoing",
            "incoming",
        ]

    def test_cannot_reject_a_completed_cash_out(
        self, client, requested, admin_headers, worker, db_session
    ):
        body, _, _ = requested
        admin, _ = admin_headers
        approve_and_burn(client, admin, body["id"], worker, db_session)

        response = client.post(
            f"/admin/cash-outs/{body['id']}/reject", headers=admin
        )
        assert response.status_code == 409

    def test_records_a_default_reason(
        self, client, requested, admin_headers
    ):
        body, _, _ = requested
        admin, _ = admin_headers

        rejected = client.post(
            f"/admin/cash-outs/{body['id']}/reject", headers=admin
        ).json()
        assert rejected["failure_reason"] == "Rejected by administrator"


class TestListing:
    def test_a_recipient_sees_only_their_own(
        self, client, funded_recipient, user_factory, auth_header_for
    ):
        headers, _ = funded_recipient
        client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        )
        other = auth_header_for(user_factory(email="stranger@example.com"))

        assert len(client.get("/wallet/cash-outs", headers=headers).json()) == 1
        assert client.get("/wallet/cash-outs", headers=other).json() == []

    def test_fetching_someone_elses_is_404_not_403(
        self, client, funded_recipient, user_factory, auth_header_for
    ):
        headers, _ = funded_recipient
        cash_out_id = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        ).json()["id"]
        other = auth_header_for(user_factory(email="stranger@example.com"))

        assert (
            client.get(
                f"/wallet/cash-outs/{cash_out_id}", headers=other
            ).status_code
            == 404
        )

    def test_the_admin_queue_can_be_filtered_by_status(
        self, client, funded_recipient, admin_headers, worker, db_session
    ):
        headers, _ = funded_recipient
        admin, _ = admin_headers
        first = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        ).json()
        client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        )
        approve_and_burn(client, admin, first["id"], worker, db_session)

        pending = client.get(
            "/admin/cash-outs?status=requested", headers=admin
        ).json()
        completed = client.get(
            "/admin/cash-outs?status=completed", headers=admin
        ).json()

        assert len(pending) == 1
        assert len(completed) == 1
        assert completed[0]["id"] == first["id"]

    def test_the_admin_queue_is_oldest_first(
        self, client, funded_recipient, admin_headers, db_session
    ):
        """The order an admin should work the queue in."""
        headers, _ = funded_recipient
        admin, _ = admin_headers
        ids = [
            client.post(
                "/wallet/cash-out",
                json={"uctusd_amount": "10", "payout_currency": "USD"},
                headers=headers,
            ).json()["id"]
            for _ in range(3)
        ]

        queue = client.get("/admin/cash-outs", headers=admin).json()
        assert [row["id"] for row in queue] == ids


class TestKeyMaterial:
    def test_the_cash_out_surface_returns_only_declared_fields(
        self, client, funded_recipient
    ):
        """
        Under pooled custody a recipient has no XRPL identity (§9.1).

        Checked against CashOutRead's declared fields rather than by
        searching the body for "seed" -- that substring test passed for
        anything leaked under a differently-named key, and would have
        failed falsely on any user-supplied text containing the word.
        """
        headers, _ = funded_recipient
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 201

        undeclared = set(response.json()) - set(CashOutRead.model_fields)
        assert not undeclared, f"cash-out returned undeclared field(s) {undeclared}"
