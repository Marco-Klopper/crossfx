"""
Integration tests for routers/remittances.py — the sender's journey
(spec §4–§8): quote, confirm cash-in, poll status, list history.

The settlement queue is faked throughout (see conftest's `fake_queue`), so
the whole suite still runs with no Redis. What is asserted about it is the
contract Track 2 published: the message carries identifiers only.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import settings
from app.models.remittance import Remittance, RemittanceStatus


@pytest.fixture(autouse=True)
def stable_rate(monkeypatch):
    """
    Flattens the mock rate to exactly 18.50 so the figures below are
    reproducible. The drift is tested in test_fx_rate_service.py; here it
    would only make the assertions vague.
    """
    monkeypatch.setattr(settings, "exchange_rate_source", "mock")
    monkeypatch.setattr(settings, "fx_mock_base_rate", 18.50)
    monkeypatch.setattr(settings, "fx_mock_volatility_bps", 0)
    monkeypatch.setattr(settings, "fixed_remittance_fee_zar", 25)
    monkeypatch.setattr(settings, "percent_fee_bps", 150)
    monkeypatch.setattr(settings, "fx_margin_bps", 100)
    monkeypatch.setattr(settings, "cashout_fee_bps", 100)
    monkeypatch.setattr(settings, "verified_daily_limit", 3000)
    monkeypatch.setattr(settings, "verified_monthly_limit", 25000)


class TestAuth:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/remittances/quote"),
            ("post", f"/remittances/{uuid.uuid4()}/confirm-cash-in"),
            ("get", "/remittances/"),
            ("get", f"/remittances/{uuid.uuid4()}"),
        ],
    )
    def test_requires_a_token(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    def test_quoting_requires_approved_kyc(
        self, client, auth_headers, beneficiary_factory
    ):
        """
        app.dependencies.require_kyc_approved, which exists for exactly
        this. An unverified sender's limits are zero anyway (spec §6), so
        letting them quote would only produce a quote they cannot fund.
        """
        headers, sender = auth_headers
        beneficiary = beneficiary_factory(sender)

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )
        assert response.status_code == 403
        assert "KYC" in response.json()["detail"]


class TestQuote:
    def test_discloses_every_figure_the_brief_requires(self, quote_factory):
        quote, _, _, beneficiary = quote_factory(amount="1000.00")

        assert Decimal(quote["zar_send_amount"]) == Decimal("1000.00")
        assert Decimal(quote["fx_rate"]) == Decimal("18.500000")
        assert Decimal(quote["transaction_fee_zar"]) == Decimal("40.00")
        assert Decimal(quote["fx_margin_zar"]) == Decimal("10.00")
        assert Decimal(quote["net_converted_zar"]) == Decimal("950.00")
        assert Decimal(quote["uctusd_amount"]) == Decimal("51.351351")
        assert Decimal(quote["effective_rate"]) > Decimal("18.500000")
        assert quote["estimated_payout_currency"] == "USD"
        assert Decimal(quote["cash_out_fee_uctusd"]) == Decimal("0.513513")
        assert Decimal(quote["estimated_payout_amount"]) == Decimal("50.83")
        assert quote["beneficiary_id"] == str(beneficiary.id)

    def test_persists_the_quote_so_it_can_be_funded(
        self, quote_factory, db_session
    ):
        quote, _, sender, _ = quote_factory()

        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        assert remittance is not None
        assert remittance.status == RemittanceStatus.QUOTED
        assert remittance.sender_id == sender.id
        assert remittance.idempotency_key

    def test_every_quote_gets_its_own_idempotency_key(
        self, quote_factory, db_session
    ):
        """
        The key is what stops one remittance being credited twice
        (spec §9.5), so two quotes sharing one would be a double-credit
        waiting to happen.
        """
        first, _, _, _ = quote_factory()
        second, _, _, _ = quote_factory()

        keys = {
            db_session.get(
                Remittance, uuid.UUID(q["remittance_id"])
            ).idempotency_key
            for q in (first, second)
        }
        assert len(keys) == 2

    def test_carries_an_expiry(self, quote_factory):
        quote, _, _, _ = quote_factory()
        expires_at = datetime.fromisoformat(quote["quote_expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        expected = datetime.now(timezone.utc) + timedelta(
            minutes=settings.quote_ttl_minutes
        )
        assert abs((expires_at - expected).total_seconds()) < 60

    def test_reports_remaining_headroom_with_this_quote_counted(
        self, quote_factory
    ):
        quote, _, _, _ = quote_factory(amount="1000.00")

        limits = quote["limits"]
        assert Decimal(limits["daily_limit_zar"]) == Decimal("3000")
        assert Decimal(limits["daily_remaining_zar"]) == Decimal("2000")
        assert Decimal(limits["monthly_remaining_zar"]) == Decimal("24000")

    def test_prices_the_payout_in_the_beneficiarys_currency(
        self, quote_factory
    ):
        quote, _, _, _ = quote_factory(preferred_payout_currency="ZAR")

        assert quote["estimated_payout_currency"] == "ZAR"
        assert Decimal(quote["estimated_payout_amount"]) == Decimal("940.50")

    def test_falls_back_to_usd_for_a_currency_the_corridor_cannot_price(
        self, quote_factory
    ):
        """
        Beneficiaries may elect EUR or GBP, but this prototype has one
        rate feed. The estimate says USD rather than inventing a number.
        """
        quote, _, _, _ = quote_factory(preferred_payout_currency="EUR")
        assert quote["estimated_payout_currency"] == "USD"

    def test_quotes_stack_against_the_daily_limit(
        self, client, quote_factory
    ):
        _, headers, _, beneficiary = quote_factory(amount="2500.00")

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )
        assert response.status_code == 403
        assert "daily" in response.json()["detail"]

    def test_an_expired_quote_releases_its_headroom(
        self, client, quote_factory, db_session
    ):
        quote, headers, _, beneficiary = quote_factory(amount="2500.00")

        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        remittance.quote_expires_at = datetime.now(
            timezone.utc
        ).replace(tzinfo=None) - timedelta(minutes=1)
        db_session.commit()

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )
        assert response.status_code == 201

    def test_rejects_someone_elses_beneficiary(
        self,
        client,
        approved_auth_headers,
        approved_user_factory,
        beneficiary_factory,
    ):
        """404, not 403 — a 403 would confirm the id exists (spec §13)."""
        headers, _ = approved_auth_headers
        other = approved_user_factory(email="other.sender@example.com")
        beneficiary = beneficiary_factory(
            other, contact="other.recipient@example.com"
        )

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )
        assert response.status_code == 404

    def test_rejects_an_unknown_beneficiary(
        self, client, approved_auth_headers
    ):
        headers, _ = approved_auth_headers
        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(uuid.uuid4()),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )
        assert response.status_code == 404

    def test_rejects_an_amount_the_fees_swallow(
        self, client, approved_auth_headers, beneficiary_factory
    ):
        headers, sender = approved_auth_headers
        beneficiary = beneficiary_factory(sender)

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "20.00",
            },
            headers=headers,
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("amount", ["0", "-100.00", "99999999.00"])
    def test_rejects_an_out_of_range_amount(
        self, client, approved_auth_headers, beneficiary_factory, amount
    ):
        headers, sender = approved_auth_headers
        beneficiary = beneficiary_factory(sender)

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": amount,
            },
            headers=headers,
        )
        assert response.status_code == 422

    def test_returns_503_when_no_rate_is_available(
        self, client, approved_auth_headers, beneficiary_factory, monkeypatch
    ):
        """
        Quoting a guessed rate would be worse than not quoting, so the
        failure is explicit and retryable.
        """
        monkeypatch.setattr(settings, "exchange_rate_source", "table")
        headers, sender = approved_auth_headers
        beneficiary = beneficiary_factory(sender)

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )
        assert response.status_code == 503


class TestConfirmCashIn:
    def test_confirms_and_queues(self, client, quote_factory, fake_queue):
        quote, headers, _, _ = quote_factory()

        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is True
        assert body["remittance"]["status"] == "queued"
        assert body["remittance"]["cash_in_method"] == "agent_cash"
        assert body["remittance"]["cash_in_confirmed_at"] is not None

    def test_the_queue_message_carries_identifiers_only(
        self, client, quote_factory, fake_queue, db_session
    ):
        """
        Track 2's publisher contract. The worker re-reads every
        authoritative figure from the row, so a tampered message cannot
        change what settles.
        """
        quote, headers, _, _ = quote_factory()
        client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "bank_transfer"},
            headers=headers,
        )

        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        assert fake_queue.published == [
            (remittance.idempotency_key, str(remittance.id))
        ]

    def test_clears_the_expiry_once_funded(
        self, client, quote_factory, fake_queue, db_session
    ):
        """The figures are locked in, so there is nothing left to expire."""
        quote, headers, _, _ = quote_factory()
        client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "card"},
            headers=headers,
        )

        db_session.expire_all()
        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        assert remittance.quote_expires_at is None

    def test_a_broken_queue_leaves_the_remittance_recoverable(
        self, client, quote_factory, broken_queue, db_session
    ):
        """
        Cash-in really was confirmed, so the response must not pretend
        otherwise — but it says `queued: false` and leaves the row in the
        state the worker and the admin retry both accept.
        """
        quote, headers, _, _ = quote_factory()

        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is False
        assert body["remittance"]["status"] == "cash_in_confirmed"
        assert "pay in again" in body["detail"]

        db_session.expire_all()
        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        assert remittance.status == RemittanceStatus.CASH_IN_CONFIRMED

    def test_rejects_an_unregistered_recipient(
        self, client, quote_factory, fake_queue
    ):
        """
        Under pooled custody the credit lands in the recipient's internal
        wallet, so they must have one. Refused at cash-in rather than at
        quote: taking the sender's cash for a transfer that cannot land
        is the failure worth preventing.
        """
        quote, headers, _, _ = quote_factory(register_recipient=False)

        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        )
        assert response.status_code == 409
        assert "no CrossFX account" in response.json()["detail"]
        assert fake_queue.published == []

    def test_rejects_an_expired_quote(
        self, client, quote_factory, fake_queue, db_session
    ):
        quote, headers, _, _ = quote_factory()
        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        remittance.quote_expires_at = datetime.now(
            timezone.utc
        ).replace(tzinfo=None) - timedelta(minutes=1)
        db_session.commit()

        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        )
        assert response.status_code == 409
        assert "expired" in response.json()["detail"]

    def test_rejects_an_unknown_cash_in_method(
        self, client, quote_factory, fake_queue
    ):
        quote, headers, _, _ = quote_factory()
        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "carrier_pigeon"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_rejects_a_remittance_that_has_already_settled(
        self, client, quote_factory, fake_queue, db_session
    ):
        quote, headers, _, _ = quote_factory()
        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        remittance.status = RemittanceStatus.SETTLED
        db_session.commit()

        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        )
        assert response.status_code == 409

    def test_confirming_twice_is_safe(
        self, client, quote_factory, fake_queue
    ):
        """
        A double-submitted form republishes rather than erroring. Safe
        because the worker claims each remittance exactly once (§9.5) —
        the second message is skipped, not settled again.
        """
        quote, headers, _, _ = quote_factory()
        path = f"/remittances/{quote['remittance_id']}/confirm-cash-in"
        body = {"cash_in_method": "agent_cash"}

        assert client.post(path, json=body, headers=headers).status_code == 200
        second = client.post(path, json=body, headers=headers)

        assert second.status_code == 200
        assert second.json()["remittance"]["status"] == "queued"
        assert len(fake_queue.published) == 2

    def test_rejects_someone_elses_remittance(
        self, client, quote_factory, approved_user_factory, auth_header_for
    ):
        quote, _, _, _ = quote_factory()
        intruder = auth_header_for(
            approved_user_factory(email="intruder@example.com")
        )

        response = client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=intruder,
        )
        assert response.status_code == 404


class TestStatusAndHistory:
    def test_reports_the_current_status(self, client, quote_factory):
        quote, headers, _, _ = quote_factory()

        response = client.get(
            f"/remittances/{quote['remittance_id']}", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "quoted"
        assert body["xrpl_tx_hash"] is None

    def test_exposes_the_settlement_hash_once_settled(
        self, client, quote_factory, db_session
    ):
        """The sender can verify the transfer on a public explorer (§9.3)."""
        quote, headers, _, _ = quote_factory()
        remittance = db_session.get(
            Remittance, uuid.UUID(quote["remittance_id"])
        )
        remittance.status = RemittanceStatus.SETTLED
        remittance.xrpl_tx_hash = "ABCDEF0123456789"
        db_session.commit()

        body = client.get(
            f"/remittances/{quote['remittance_id']}", headers=headers
        ).json()
        assert body["status"] == "settled"
        assert body["xrpl_tx_hash"] == "ABCDEF0123456789"

    def test_unknown_id_is_404(self, client, approved_auth_headers):
        headers, _ = approved_auth_headers
        assert (
            client.get(
                f"/remittances/{uuid.uuid4()}", headers=headers
            ).status_code
            == 404
        )

    def test_history_is_newest_first(self, client, quote_factory):
        quote_factory(amount="100.00")
        second, headers, _, _ = quote_factory(amount="200.00")

        body = client.get("/remittances/", headers=headers).json()

        assert len(body) == 2
        assert body[0]["id"] == second["remittance_id"]

    def test_history_is_empty_for_a_new_sender(
        self, client, approved_auth_headers
    ):
        headers, _ = approved_auth_headers
        assert client.get("/remittances/", headers=headers).json() == []

    def test_history_is_isolated_per_sender(
        self, client, quote_factory, approved_user_factory, auth_header_for
    ):
        quote_factory()
        intruder = auth_header_for(
            approved_user_factory(email="nosy@example.com")
        )

        assert client.get("/remittances/", headers=intruder).json() == []
