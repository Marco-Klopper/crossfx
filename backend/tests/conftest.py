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
# Assigned, NOT setdefault: a DATABASE_URL exported in the developer's
# shell would otherwise survive, and app/database.py:10 binds its engine
# to settings.database_url at import time. Every test overrides get_db,
# so nothing reads that engine today — but one test that forgets to, or
# one worker call without an explicit db=, would write to the real
# database. Pinning it here makes that impossible rather than unlikely.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("PRIVATE_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("UCTUSD_ISSUER_ADDRESS", "rTestIssuerAddressXXXXXXXXXXXXXXXX")
os.environ.setdefault(
    "UCTUSD_CURRENCY_CODE", "5543545553440000000000000000000000000000"
)

import pytest
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.beneficiary import Beneficiary
from app.models.remittance import Remittance, RemittanceStatus
from app.models.user import KYCStatus, User
from app.models.wallet import PlatformWallet, PoolRole
from app.services import cashin_cashout_service
from app.security.encryption import encrypt_seed
from app.security.hashing import hash_password
from app.security.jwt import create_access_token


@pytest.fixture(scope="session", autouse=True)
def _cheap_password_hashing():
    """
    Drops the bcrypt cost factor for the suite.

    passlib's default cost of 12 is ~0.29s per hash on this hardware, and
    the factories below mint two or three users per test — which made the
    suite roughly 95% key derivation and 73 seconds long. The tests care
    that hashing round-trips and that a wrong password fails, neither of
    which depends on the cost. Production is untouched: nothing outside
    this fixture changes app.security.hashing.pwd_context, and the
    application imports it fresh.
    """
    from passlib.context import CryptContext

    from app.security import hashing

    original = hashing.pwd_context
    hashing.pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=4)
    yield
    hashing.pwd_context = original


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


# ---------------------------------------------------------------------------
# Track 2 (pooled custody / settlement) fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def pool_wallets(db_session):
    """
    The two pooled corridor wallets the settlement worker settles
    between, with seeds encrypted exactly as
    scripts/init_platform_wallets.py stores them — so the tests
    exercise the real decrypt path rather than a shortcut.
    """
    send_pool = PlatformWallet(
        role=PoolRole.SEND_POOL,
        xrpl_address="rSendPoolAddressXXXXXXXXXXXXXXXXX",
        xrpl_encrypted_seed=encrypt_seed("sEdSendPoolSeedNotReal"),
    )
    payout_pool = PlatformWallet(
        role=PoolRole.PAYOUT_POOL,
        xrpl_address="rPayoutPoolAddressXXXXXXXXXXXXXXX",
        xrpl_encrypted_seed=encrypt_seed("sEdPayoutPoolSeedNotReal"),
    )
    db_session.add_all([send_pool, payout_pool])
    db_session.commit()
    return send_pool, payout_pool


@pytest.fixture()
def remittance_factory(db_session, user_factory):
    """
    remittance_factory() -> (remittance, sender, recipient).

    Wires up the whole chain a settlement needs: a sender, a registered
    recipient, and a Beneficiary whose `contact` is the recipient's
    email — which is how the worker resolves who to credit.
    """
    counter = {"n": 0}

    def _make(
        zar_send_amount=Decimal("1000.00"),
        uctusd_amount=Decimal("52.500000"),
        status=RemittanceStatus.CASH_IN_CONFIRMED,
        recipient_email=None,
        register_recipient=True,
    ):
        counter["n"] += 1
        n = counter["n"]
        sender = user_factory(email=f"sender{n}@example.com")
        recipient_email = recipient_email or f"recipient{n}@example.com"
        recipient = (
            user_factory(email=recipient_email)
            if register_recipient
            else None
        )

        beneficiary = Beneficiary(
            sender_id=sender.id,
            full_name="Recipient Name",
            contact=recipient_email,
            country="US",
            preferred_payout_currency="USD",
            relationship_to_sender="family",
        )
        db_session.add(beneficiary)
        db_session.commit()
        db_session.refresh(beneficiary)

        remittance = Remittance(
            idempotency_key=f"idem-key-{n}",
            sender_id=sender.id,
            beneficiary_id=beneficiary.id,
            zar_send_amount=zar_send_amount,
            fx_rate_used=Decimal("18.500000"),
            transaction_fee_zar=Decimal("25.00"),
            fx_margin_zar=Decimal("10.00"),
            net_converted_zar=Decimal("965.00"),
            uctusd_amount=uctusd_amount,
            status=status,
        )
        db_session.add(remittance)
        db_session.commit()
        db_session.refresh(remittance)
        return remittance, sender, recipient

    return _make


# ---------------------------------------------------------------------------
# Track 3 (FX, fees, remittance flow) fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def approved_user_factory(db_session, user_factory):
    """
    A user whose KYC has been approved. Track 3's sender endpoints are
    gated on require_kyc_approved, so the plain `auth_headers` user —
    who is NOT_STARTED — gets a 403 from all of them.
    """

    def _make(email="approved@example.com", **kwargs):
        user = user_factory(email=email, **kwargs)
        user.kyc_status = KYCStatus.APPROVED
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make


@pytest.fixture()
def approved_auth_headers(approved_user_factory):
    """(headers, user) for a KYC-approved sender."""
    user = approved_user_factory(email="approved.sender@example.com")
    return _auth_header(user), user


@pytest.fixture()
def beneficiary_factory(db_session):
    """
    beneficiary_factory(sender, contact=...) -> Beneficiary.

    `contact` doubles as the recipient's login email: that is how the
    settlement worker resolves who to credit (spec §9.1), so a test that
    wants a settleable remittance must register a user under the same
    address.
    """

    def _make(
        sender,
        contact="recipient@example.com",
        full_name="Recipient Name",
        country="United States",
        preferred_payout_currency="USD",
        relationship_to_sender="Brother",
    ):
        beneficiary = Beneficiary(
            sender_id=sender.id,
            full_name=full_name,
            contact=contact,
            country=country,
            preferred_payout_currency=preferred_payout_currency,
            relationship_to_sender=relationship_to_sender,
        )
        db_session.add(beneficiary)
        db_session.commit()
        db_session.refresh(beneficiary)
        return beneficiary

    return _make


class FakeSettlementQueue:
    """
    Stands in for Redis Streams. Records what was published so a test can
    assert the message carried only identifiers, and can be told to fail
    so the "queue is down" branch is exercised without stopping a broker.
    """

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.published: list[tuple[str, str]] = []
        self.fail_with = fail_with

    def publish(self, idempotency_key, remittance_id) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.published.append((str(idempotency_key), str(remittance_id)))
        return f"entry-{len(self.published)}"


@pytest.fixture()
def fake_queue(monkeypatch):
    """
    Replaces the SettlementQueue the cash-in path constructs, so the whole
    suite still runs with no Redis (backend/README.md §8).
    """
    queue = FakeSettlementQueue()
    monkeypatch.setattr(
        cashin_cashout_service, "SettlementQueue", lambda: queue
    )
    return queue


@pytest.fixture()
def broken_queue(monkeypatch):
    """A queue that refuses every publish."""
    queue = FakeSettlementQueue(
        fail_with=ConnectionError("Connection refused by localhost:6379")
    )
    monkeypatch.setattr(
        cashin_cashout_service, "SettlementQueue", lambda: queue
    )
    return queue


@pytest.fixture()
def quote_factory(client, approved_auth_headers, beneficiary_factory, user_factory):
    """
    quote_factory(amount="1000.00", register_recipient=True)
      -> (quote_body, headers, sender, beneficiary)

    Posts a real /remittances/quote, so the row under test is one the API
    actually produced rather than one hand-built to match.
    """
    counter = {"n": 0}

    def _make(amount="1000.00", register_recipient=True, **beneficiary_kwargs):
        headers, sender = approved_auth_headers
        counter["n"] += 1
        contact = beneficiary_kwargs.pop(
            "contact", f"t3-recipient{counter['n']}@example.com"
        )
        if register_recipient:
            user_factory(email=contact)
        beneficiary = beneficiary_factory(
            sender, contact=contact, **beneficiary_kwargs
        )
        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": amount,
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        return response.json(), headers, sender, beneficiary

    return _make
