"""
Integration tests for routers/wallet.py under pooled custody.

/cash-out and /cash-outs live in test_cash_out.py, with the admin
approval half of that flow they only make sense beside.
"""
from decimal import Decimal

import pytest

from app.config import settings
from app.schemas.wallet import WalletBalanceRead, WalletTransactionRead
from app.services.ledger import Ledger


@pytest.fixture()
def ledger(db_session):
    return Ledger(db_session)


@pytest.fixture(autouse=True)
def pinned_currencies(monkeypatch):
    """
    Pins SUPPORTED_CURRENCIES for this module.

    The balance assertions name the exact set, and app/config.py reads
    .env at import - so adding a currency in a developer's own .env
    failed tests here for a reason unrelated to the code under test.
    """
    monkeypatch.setattr(settings, "supported_currencies", "UCTUSD,USD,ZAR")


class TestAuth:
    @pytest.mark.parametrize(
        "path", ["/wallet/balance", "/wallet/transactions"]
    )
    def test_requires_a_token(self, client, path):
        assert client.get(path).status_code == 401


class TestBalance:
    def test_lazily_creates_a_zero_wallet(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.get("/wallet/balance", headers=headers)
        assert resp.status_code == 200
        assert float(resp.json()["uctusd_balance"]) == 0.0

    def test_lists_every_supported_currency_even_when_unheld(
        self, client, auth_headers
    ):
        """
        The recipient UI should render a stable set of currencies
        rather than one that appears a row at a time as balances are
        first touched.
        """
        headers, _ = auth_headers
        body = client.get("/wallet/balance", headers=headers).json()

        assert {b["currency"] for b in body["balances"]} == {
            "UCTUSD",
            "USD",
            "ZAR",
        }
        assert all(float(b["amount"]) == 0.0 for b in body["balances"])

    def test_reflects_ledger_credits(
        self, client, auth_headers, ledger, db_session
    ):
        headers, user = auth_headers
        ledger.credit(ledger.wallet_for(user), "UCTUSD", Decimal("42.5"))
        db_session.commit()

        body = client.get("/wallet/balance", headers=headers).json()
        assert float(body["uctusd_balance"]) == 42.5

    def test_is_multi_currency(
        self, client, auth_headers, ledger, db_session
    ):
        """The point of the internal ledger: one wallet, many currencies."""
        headers, user = auth_headers
        wallet = ledger.wallet_for(user)
        ledger.credit(wallet, "UCTUSD", Decimal("100"))
        ledger.credit(wallet, "USD", Decimal("30"))
        db_session.commit()

        body = client.get("/wallet/balance", headers=headers).json()
        by_currency = {
            b["currency"]: float(b["amount"]) for b in body["balances"]
        }
        assert by_currency == {"UCTUSD": 100.0, "USD": 30.0, "ZAR": 0.0}
        assert float(body["uctusd_balance"]) == 100.0


class TestTransactions:
    def test_empty_when_no_activity(self, client, auth_headers):
        headers, _ = auth_headers
        resp = client.get("/wallet/transactions", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_newest_first(
        self, client, auth_headers, ledger, db_session
    ):
        headers, user = auth_headers
        wallet = ledger.wallet_for(user)
        for amount in ("10", "20"):
            ledger.credit(wallet, "UCTUSD", Decimal(amount))
            db_session.commit()

        body = client.get("/wallet/transactions", headers=headers).json()
        assert [float(t["amount"]) for t in body] == [20.0, 10.0]
        assert all(t["currency"] == "UCTUSD" for t in body)

    def test_exposes_the_fields_the_brief_requires(
        self, client, auth_headers, ledger, db_session
    ):
        headers, user = auth_headers
        ledger.credit(
            ledger.wallet_for(user),
            "UCTUSD",
            Decimal("7"),
            xrpl_tx_hash="ABCDEF123",
        )
        db_session.commit()

        entry = client.get(
            "/wallet/transactions", headers=headers
        ).json()[0]
        # incoming/outgoing, status, date, XRP Ledger transaction hash.
        assert entry["direction"] == "incoming"
        assert entry["status"] == "success"
        assert entry["xrpl_tx_hash"] == "ABCDEF123"
        assert entry["created_at"] is not None

    def test_isolated_per_user(
        self,
        client,
        auth_headers,
        user_factory,
        auth_header_for,
        ledger,
        db_session,
    ):
        headers, user = auth_headers
        ledger.credit(ledger.wallet_for(user), "UCTUSD", Decimal("5"))
        db_session.commit()

        other = auth_header_for(user_factory(email="other@example.com"))

        assert client.get("/wallet/transactions", headers=other).json() == []
        assert (
            len(client.get("/wallet/transactions", headers=headers).json())
            == 1
        )


class TestKeyMaterial:
    @pytest.mark.parametrize(
        "path,model",
        [
            ("/wallet/balance", WalletBalanceRead),
            ("/wallet/transactions", WalletTransactionRead),
        ],
    )
    def test_returns_only_the_declared_fields(
        self, client, auth_headers, pool_wallets, path, model
    ):
        """
        Under pooled custody a user has no XRPL identity, and the brief
        forbids ever returning key material.

        Asserted against the declared response model rather than by
        searching the body for the string "seed". That substring check
        passed for anything leaked under a key not containing the word --
        `secret`, `sk`, `signing_key` -- and would have failed falsely the
        first time a user's address or a beneficiary's name contained it.
        Pinning the key set is the assertion that actually holds: a field
        added to a schema by mistake fails here.
        """
        headers, _ = auth_headers
        response = client.get(path, headers=headers)
        assert response.status_code == 200

        allowed = set(model.model_fields)
        payload = response.json()
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            assert set(row) <= allowed, (
                f"{path} returned undeclared field(s) {set(row) - allowed}"
            )

    @pytest.mark.parametrize(
        "path", ["/wallet/balance", "/wallet/transactions"]
    )
    def test_never_leaks_a_pool_seed(
        self, client, auth_headers, pool_wallets, path
    ):
        """
        The complementary check, and the one with teeth: the actual
        encrypted seeds and pool addresses exist in this database, so
        look for those exact values rather than for a word.
        """
        headers, _ = auth_headers
        body = client.get(path, headers=headers).text
        for wallet in pool_wallets:
            assert wallet.xrpl_encrypted_seed not in body
            assert wallet.xrpl_address not in body
