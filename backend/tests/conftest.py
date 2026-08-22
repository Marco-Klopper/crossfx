"""
Shared pytest fixtures.

Import-order gotcha (read before adding new fixtures): app.config.Settings()
and app.security.encryption's module-level Fernet(...) both run validation at
IMPORT time, not at call time. So every required env var must be set in
os.environ before the FIRST `import app...` anywhere in the test suite — which
is why they're set here, at the very top of conftest.py, ahead of any app
import. Adding an `import app.something` above the os.environ block will
reintroduce the crash this file exists to prevent.
"""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite://")  # unused directly; tests override get_db
os.environ.setdefault("PRIVATE_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("RLUSD_ISSUER_ADDRESS", "rTestIssuerAddressXXXXXXXXXXXXXXXX")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.user import User
from app.security.hashing import hash_password
from app.security.jwt import create_access_token


@pytest.fixture()
def engine():
    """
    A fresh in-memory SQLite database per test. StaticPool keeps the same
    underlying connection alive for the engine's lifetime — plain in-memory
    SQLite is otherwise per-connection, so a second checkout would see an
    empty database.
    """
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def db_session(engine):
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestSessionLocal()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def user_factory(db_session):
    """Call as user_factory(email=..., password=..., is_admin=...) -> User (persisted)."""

    def _make(email="user@example.com", password="password123", full_name="Test User", is_admin=False):
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            is_admin=is_admin,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make


def _auth_header(user: User) -> dict:
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def auth_header_for():
    """auth_header_for(user) -> headers dict, for a user built outside auth_headers/admin_headers."""
    return _auth_header


@pytest.fixture()
def auth_headers(user_factory):
    """A plain (non-admin, not-KYC'd) user's Authorization header."""
    user = user_factory(email="sender@example.com")
    return _auth_header(user), user


@pytest.fixture()
def admin_headers(user_factory):
    admin = user_factory(email="admin@example.com", is_admin=True)
    return _auth_header(admin), admin
