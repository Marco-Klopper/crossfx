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
from app.services.ledger import Ledger


@pytest.fixture(autouse=True)
def stable_rate(monkeypatch):
    monkeypatch.setattr(settings, "exchange_rate_source", "mock")
    monkeypatch.setattr(settings, "fx_mock_base_rate", 18.50)
    monkeypatch.setattr(settings, "fx_mock_volatility_bps", 0)
    monkeypatch.setattr(settings, "cashout_fee_bps", 100)


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

    def test_credits_the_fiat_leg(
        self, client, requested, admin_headers, db_session
    ):
        body, _, user = requested
        admin, _ = admin_headers

        response = client.post(
            f"/admin/cash-outs/{body['id']}/approve", headers=admin
        )

        assert response.status_code == 200
        approved = response.json()
        assert approved["status"] == "completed"
        assert approved["approved_at"] is not None
        assert approved["completed_at"] is not None

        db_session.expire_all()
        assert _balance(db_session, user, "USD") == Decimal("49.500000")
        assert _balance(db_session, user, "UCTUSD") == Decimal("50.000000")

    def test_the_fee_stays_with_the_platform(
        self, client, requested, admin_headers, db_session
    ):
        """
        50 UCTUSD left the recipient, 49.50 came back as USD. The 0.50
        difference is the cash-out fee, and it is not credited anywhere —
        it is the platform's revenue (spec §4).
        """
        body, _, user = requested
        admin, _ = admin_headers
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

        db_session.expire_all()
        assert (
            _balance(db_session, user, "UCTUSD")
            + Decimal(body["net_uctusd"])
            + Decimal(body["cash_out_fee_uctusd"])
            == Decimal("100.000000")
        )

    def test_both_legs_appear_in_the_wallet_history(
        self, client, requested, admin_headers
    ):
        body, headers, _ = requested
        admin, _ = admin_headers
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

        history = client.get("/wallet/transactions", headers=headers).json()
        directions = {(t["direction"], t["currency"]) for t in history}
        assert ("outgoing", "UCTUSD") in directions
        assert ("incoming", "USD") in directions

    def test_cannot_be_approved_twice(
        self, client, requested, admin_headers, db_session
    ):
        body, _, user = requested
        admin, _ = admin_headers
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

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
        self, client, requested, admin_headers
    ):
        body, _, _ = requested
        admin, _ = admin_headers
        client.post(f"/admin/cash-outs/{body['id']}/approve", headers=admin)

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
        self, client, funded_recipient, admin_headers
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
        client.post(f"/admin/cash-outs/{first['id']}/approve", headers=admin)

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
    def test_the_cash_out_surface_exposes_no_key_material(
        self, client, funded_recipient
    ):
        """Under pooled custody a recipient has no XRPL identity (§9.1)."""
        headers, _ = funded_recipient
        body = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10", "payout_currency": "USD"},
            headers=headers,
        ).text

        assert "seed" not in body.lower()
        assert "xrpl_address" not in body
