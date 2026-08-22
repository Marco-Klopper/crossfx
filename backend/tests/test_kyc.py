"""
Integration tests for routers/kyc.py.
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


def test_apply_moves_user_and_application_to_pending(client, auth_headers):
    headers, user = auth_headers
    resp = client.post("/kyc/apply", json=_valid_application(), headers=headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"

    me = client.get("/auth/me", headers=headers).json()
    assert me["kyc_status"] == "pending"


def test_double_apply_conflicts(client, auth_headers):
    headers, _ = auth_headers
    client.post("/kyc/apply", json=_valid_application(), headers=headers)
    resp = client.post("/kyc/apply", json=_valid_application(), headers=headers)
    assert resp.status_code == 409


def test_under_18_rejected(client, auth_headers):
    headers, _ = auth_headers
    resp = client.post(
        "/kyc/apply", json=_valid_application(date_of_birth="2015-01-01"), headers=headers
    )
    assert resp.status_code == 422


def test_future_dob_rejected(client, auth_headers):
    headers, _ = auth_headers
    resp = client.post(
        "/kyc/apply", json=_valid_application(date_of_birth="2999-01-01"), headers=headers
    )
    assert resp.status_code == 422


def test_status_before_applying(client, auth_headers):
    headers, _ = auth_headers
    resp = client.get("/kyc/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["kyc_status"] == "not_started"
    assert body["latest_application"] is None


def test_status_after_applying(client, auth_headers):
    headers, _ = auth_headers
    client.post("/kyc/apply", json=_valid_application(), headers=headers)
    resp = client.get("/kyc/status", headers=headers)
    body = resp.json()
    assert body["kyc_status"] == "pending"
    assert body["latest_application"]["status"] == "pending"


def test_apply_requires_auth(client):
    resp = client.post("/kyc/apply", json=_valid_application())
    assert resp.status_code == 401
