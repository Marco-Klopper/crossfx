"""
Integration tests for routers/admin.py: the require_admin gate, the KYC review
queue, and the approve/reject flow (including its effect on User.kyc_status).
"""


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
