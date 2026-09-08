"""
Integration tests for routers/wallet.py under pooled custody.

/cash-out is intentionally not covered — it is still blocked on Track
3's fee_service and the cash-out sub-record (see the docstring on
request_cash_out).
"""
from decimal import Decimal

import pytest

from app.services.ledger import Ledger


@pytest.fixture()
def ledger(db_session):
    return Ledger(db_session)


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
        assert float(resp.json()["rlusd_balance"]) == 0.0

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
            "RLUSD",
            "USD",
            "ZAR",
        }
        assert all(float(b["amount"]) == 0.0 for b in body["balances"])

    def test_reflects_ledger_credits(
        self, client, auth_headers, ledger, db_session
    ):
        headers, user = auth_headers
        ledger.credit(ledger.wallet_for(user), "RLUSD", Decimal("42.5"))
        db_session.commit()

        body = client.get("/wallet/balance", headers=headers).json()
        assert float(body["rlusd_balance"]) == 42.5

    def test_is_multi_currency(
        self, client, auth_headers, ledger, db_session
    ):
        """The point of the internal ledger: one wallet, many currencies."""
        headers, user = auth_headers
        wallet = ledger.wallet_for(user)
        ledger.credit(wallet, "RLUSD", Decimal("100"))
        ledger.credit(wallet, "USD", Decimal("30"))
        db_session.commit()

        body = client.get("/wallet/balance", headers=headers).json()
        by_currency = {
            b["currency"]: float(b["amount"]) for b in body["balances"]
        }
        assert by_currency == {"RLUSD": 100.0, "USD": 30.0, "ZAR": 0.0}
        assert float(body["rlusd_balance"]) == 100.0


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
            ledger.credit(wallet, "RLUSD", Decimal(amount))
            db_session.commit()

        body = client.get("/wallet/transactions", headers=headers).json()
        assert [float(t["amount"]) for t in body] == [20.0, 10.0]
        assert all(t["currency"] == "RLUSD" for t in body)

    def test_exposes_the_fields_the_brief_requires(
        self, client, auth_headers, ledger, db_session
    ):
        headers, user = auth_headers
        ledger.credit(
            ledger.wallet_for(user),
            "RLUSD",
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
        ledger.credit(ledger.wallet_for(user), "RLUSD", Decimal("5"))
        db_session.commit()

        other = auth_header_for(user_factory(email="other@example.com"))

        assert client.get("/wallet/transactions", headers=other).json() == []
        assert (
            len(client.get("/wallet/transactions", headers=headers).json())
            == 1
        )


class TestKeyMaterial:
    @pytest.mark.parametrize(
        "path", ["/wallet/balance", "/wallet/transactions"]
    )
    def test_exposes_no_seed_or_address(self, client, auth_headers, path):
        """
        Under pooled custody a user has no XRPL identity, and the brief
        forbids ever returning key material.
        """
        headers, _ = auth_headers
        body = client.get(path, headers=headers).text
        assert "seed" not in body.lower()
        assert "xrpl_address" not in body
