"""
Integration tests for routers/admin.py: the require_admin gate, the KYC review
queue, and the approve/reject flow (including its effect on User.kyc_status).

The cash-out review routes are covered in test_cash_out.py, alongside the
recipient half of that flow. What is here is the admin cash-in confirmation
(spec §8) — the mock payment-service hook, and the manual retry for a
settlement message that never reached the queue.
"""
import uuid

from app.models.remittance import Remittance, RemittanceStatus


def _valid_application(**overrides):
    payload = {
        "full_name": "Alice Sender",
        "date_of_birth": "1990-05-15",
        "nationality": "South African",
        "identification_number": "9005155800086",
        "residential_address": "1 Long St, Cape Town",
        "mobile_number": "+27821234567",
        "email": "sender@example.com",
        "source_of_funds": "Salary",
    }
    payload.update(overrides)
    return payload


ADMIN_ROUTES = [
    ("GET", "/admin/kyc/applications"),
    ("POST", "/admin/kyc/00000000-0000-0000-0000-000000000000/approve"),
    ("POST", "/admin/kyc/00000000-0000-0000-0000-000000000000/reject"),
    ("POST", "/admin/remittances/00000000-0000-0000-0000-000000000000/confirm-payment"),
    ("POST", "/admin/cash-outs/00000000-0000-0000-0000-000000000000/approve"),
    ("POST", "/admin/cash-outs/00000000-0000-0000-0000-000000000000/reject"),
    ("GET", "/admin/cash-outs"),
]


def test_non_admin_forbidden_from_every_admin_route(client, auth_headers):
    headers, _ = auth_headers
    for method, path in ADMIN_ROUTES:
        resp = client.request(method, path, headers=headers)
        assert resp.status_code == 403, f"{method} {path} should 403 for a non-admin"


def test_admin_routes_require_auth_at_all(client):
    for method, path in ADMIN_ROUTES:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should 401 with no token"


def test_review_queue_lists_pending(client, auth_headers, admin_headers):
    headers, _ = auth_headers
    admin_hdrs, _ = admin_headers
    client.post("/kyc/apply", json=_valid_application(), headers=headers)

    resp = client.get("/admin/kyc/applications?status=pending", headers=admin_hdrs)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["email"] == "sender@example.com"


def test_approve_flips_both_statuses_and_stamps_reviewer(client, auth_headers, admin_headers):
    headers, user = auth_headers
    admin_hdrs, admin = admin_headers
    app_id = client.post("/kyc/apply", json=_valid_application(), headers=headers).json()["id"]

    resp = client.post(f"/admin/kyc/{app_id}/approve", headers=admin_hdrs)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reviewed_at"] is not None

    me = client.get("/auth/me", headers=headers).json()
    assert me["kyc_status"] == "approved"
    assert me["limits"] == {"daily_limit_zar": 3000.0, "monthly_limit_zar": 25000.0}


def test_approve_twice_conflicts(client, auth_headers, admin_headers):
    headers, _ = auth_headers
    admin_hdrs, _ = admin_headers
    app_id = client.post("/kyc/apply", json=_valid_application(), headers=headers).json()["id"]

    client.post(f"/admin/kyc/{app_id}/approve", headers=admin_hdrs)
    resp = client.post(f"/admin/kyc/{app_id}/approve", headers=admin_hdrs)
    assert resp.status_code == 409


def test_reject_records_reason_and_flips_user_status(client, auth_headers, admin_headers):
    headers, _ = auth_headers
    admin_hdrs, _ = admin_headers
    app_id = client.post("/kyc/apply", json=_valid_application(), headers=headers).json()["id"]

    resp = client.post(
        f"/admin/kyc/{app_id}/reject", json={"reason": "ID number did not match records"}, headers=admin_hdrs
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "ID number did not match records"

    me = client.get("/auth/me", headers=headers).json()
    assert me["kyc_status"] == "rejected"


def test_approve_unknown_application_404s(client, admin_headers):
    admin_hdrs, _ = admin_headers
    resp = client.post(
        "/admin/kyc/00000000-0000-0000-0000-000000000000/approve", headers=admin_hdrs
    )
    assert resp.status_code == 404


class TestConfirmZarPayment:
    """The mock payment-service hook that starts settlement (spec §8)."""

    def test_confirms_and_queues_a_quoted_remittance(
        self, client, quote_factory, admin_headers, fake_queue, db_session
    ):
        quote, _, _, _ = quote_factory()
        admin, _ = admin_headers

        response = client.post(
            f"/admin/remittances/{quote['remittance_id']}/confirm-payment",
            headers=admin,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is True
        assert body["remittance"]["status"] == "queued"
        # No body means the admin is confirming a deposit they can see.
        assert body["remittance"]["cash_in_method"] == "bank_transfer"
        assert len(fake_queue.published) == 1

    def test_accepts_an_explicit_method(
        self, client, quote_factory, admin_headers, fake_queue
    ):
        quote, _, _, _ = quote_factory()
        admin, _ = admin_headers

        body = client.post(
            f"/admin/remittances/{quote['remittance_id']}/confirm-payment",
            json={"cash_in_method": "agent_cash"},
            headers=admin,
        ).json()

        assert body["remittance"]["cash_in_method"] == "agent_cash"

    def test_republishes_an_already_confirmed_remittance(
        self, client, quote_factory, admin_headers, fake_queue, db_session
    ):
        """
        The recovery path: the sender confirmed cash-in while the queue
        was down, so the row is CASH_IN_CONFIRMED with no message behind
        it. An admin re-running this is what gets it moving, and it is
        safe because the worker claims each remittance exactly once (§9.5).
        """
        quote, headers, _, _ = quote_factory()
        admin, _ = admin_headers

        # Queue down: confirmed but not queued.
        fake_queue.fail_with = ConnectionError("Connection refused")
        first = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        ).json()
        assert first["queued"] is False

        # Queue back: the admin retry publishes it.
        fake_queue.fail_with = None
        retry = client.post(
            f"/admin/remittances/{quote['remittance_id']}/confirm-payment",
            headers=admin,
        ).json()

        assert retry["queued"] is True
        assert retry["remittance"]["status"] == "queued"
        assert len(fake_queue.published) == 1

    def test_reports_a_queue_that_is_still_down(
        self, client, quote_factory, admin_headers, broken_queue, db_session
    ):
        quote, _, _, _ = quote_factory()
        admin, _ = admin_headers

        body = client.post(
            f"/admin/remittances/{quote['remittance_id']}/confirm-payment",
            headers=admin,
        ).json()

        assert body["queued"] is False
        assert body["remittance"]["status"] == "cash_in_confirmed"

    def test_rejects_an_unregistered_recipient(
        self, client, quote_factory, admin_headers, fake_queue
    ):
        quote, _, _, _ = quote_factory(register_recipient=False)
        admin, _ = admin_headers

        response = client.post(
            f"/admin/remittances/{quote['remittance_id']}/confirm-payment",
            headers=admin,
        )
        assert response.status_code == 409

    def test_rejects_a_settled_remittance(
        self, client, quote_factory, admin_headers, fake_queue, db_session
    ):
        quote, _, _, _ = quote_factory()
        admin, _ = admin_headers
        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        remittance.status = RemittanceStatus.SETTLED
        db_session.commit()

        response = client.post(
            f"/admin/remittances/{quote['remittance_id']}/confirm-payment",
            headers=admin,
        )
        assert response.status_code == 409

    def test_unknown_remittance_is_404(self, client, admin_headers):
        admin, _ = admin_headers
        response = client.post(
            f"/admin/remittances/{uuid.uuid4()}/confirm-payment",
            headers=admin,
        )
        assert response.status_code == 404
