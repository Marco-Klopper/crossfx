"""
Integration tests for routers/auth.py.
"""


def _register(client, email="alice@example.com", password="password123", full_name="Alice"):
    return client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )


def test_register_happy_path(client):
    resp = _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["kyc_status"] == "not_started"
    assert body["is_admin"] is False
    assert "hashed_password" not in body  # never leak the hash


def test_register_duplicate_email_conflicts(client):
    _register(client)
    resp = _register(client)
    assert resp.status_code == 409


def test_register_weak_password_rejected(client):
    resp = _register(client, password="short")
    assert resp.status_code == 422


def test_login_success_returns_token(client):
    _register(client)
    resp = client.post("/auth/login", json={"email": "alice@example.com", "password": "password123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_wrong_password_returns_generic_401(client):
    _register(client)
    resp = client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password"


def test_login_unknown_email_returns_same_generic_401(client):
    """Same message/status as a wrong password — the endpoint must not be a user-enumeration oracle."""
    resp = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password"


def test_form_login_works_for_swagger_authorize(client):
    _register(client)
    resp = client.post(
        "/auth/token", data={"username": "alice@example.com", "password": "password123"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_profile_and_limits(client, auth_headers):
    headers, user = auth_headers
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == user.email
    assert body["kyc_status"] == "not_started"
    # Unverified users get the zero limits from app.config.settings.
    assert body["limits"] == {"daily_limit_zar": 0.0, "monthly_limit_zar": 0.0}


def test_logout_is_honest_about_being_client_side(client):
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
    assert "cannot revoke" in resp.json()["detail"]
