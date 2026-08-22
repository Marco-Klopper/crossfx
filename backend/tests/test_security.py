"""
Unit tests for app.security.hashing and app.security.jwt. No DB, no HTTP —
these two modules are deliberately framework-free so they can be tested here
in isolation.
"""
import time

import pytest

from app.security.hashing import hash_password, verify_password
from app.security.jwt import InvalidTokenError, create_access_token, decode_access_token


def test_hash_and_verify_round_trip():
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed)


def test_verify_rejects_wrong_password():
    hashed = hash_password("correct-horse-battery-staple")
    assert not verify_password("wrong-password", hashed)


def test_jwt_round_trip():
    token = create_access_token(subject="user-123")
    assert decode_access_token(token) == "user-123"


def test_jwt_rejects_tampered_signature():
    token = create_access_token(subject="user-123")
    tampered = token[:-4] + "abcd"
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_jwt_rejects_garbage():
    with pytest.raises(InvalidTokenError):
        decode_access_token("not-a-jwt-at-all")


def test_jwt_rejects_expired_token(monkeypatch):
    # Force an already-expired token by shrinking the expiry window to 0 minutes.
    from app import config

    monkeypatch.setattr(config.settings, "access_token_expire_minutes", 0)
    token = create_access_token(subject="user-123")
    time.sleep(1.1)  # cross the second boundary the exp claim is truncated to
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)
