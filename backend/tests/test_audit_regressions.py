"""
Regression tests for the defects this audit found.

Each one pins a specific bug that was real, reproducible, and would have
come back the moment someone refactored the code around it. They are kept
together rather than scattered so that the audit's findings stay legible
as a set — every test here names the behaviour it is preventing.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.remittance import (
    LIMIT_CONSUMING_STATUSES,
    Remittance,
    RemittanceStatus,
)
from app.models.user import KYCStatus
from app.services import cashin_cashout_service, fx_rate_service, limits_service
from app.services.ledger import Direction, EntryStatus, Ledger


@pytest.fixture(autouse=True)
def stable_pricing(monkeypatch):
    monkeypatch.setattr(settings, "exchange_rate_source", "mock")
    monkeypatch.setattr(settings, "fx_mock_base_rate", Decimal("18.50"))
    monkeypatch.setattr(settings, "fx_mock_volatility_bps", 0)
    monkeypatch.setattr(settings, "fixed_remittance_fee_zar", Decimal("25"))
    monkeypatch.setattr(settings, "percent_fee_bps", 150)
    monkeypatch.setattr(settings, "fx_margin_bps", 100)
    monkeypatch.setattr(settings, "cashout_fee_bps", 100)
    monkeypatch.setattr(settings, "verified_daily_limit", Decimal("3000"))
    monkeypatch.setattr(settings, "verified_monthly_limit", Decimal("25000"))


# ---------------------------------------------------------------------------
# Money-path input validation
# ---------------------------------------------------------------------------
class TestAmountBounds:
    """
    CashOutRequest.uctusd_amount had no upper bound. Decimal accepts
    "1e1000" happily, and fee_service._token() then raised
    decimal.InvalidOperation out of quantize() — which routers/wallet.py
    did not catch. Any logged-in caller could produce a 500 on a money
    endpoint, for free, as often as they liked.
    """

    @pytest.mark.parametrize(
        "amount", ["1e1000", "1E+999", "9" * 40, "1e100000"]
    )
    def test_an_enormous_cash_out_is_refused_not_a_500(
        self, client, auth_headers, amount
    ):
        headers, _ = auth_headers
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": amount, "payout_currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 422, response.text
        assert response.status_code != 500

    def test_a_cash_out_within_the_column_is_still_accepted(
        self, client, auth_headers
    ):
        """The bound must not have narrowed anything legitimate."""
        headers, _ = auth_headers
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10.000000", "payout_currency": "USD"},
            headers=headers,
        )
        # 400 = insufficient funds, which is a *priced* refusal: the request
        # got all the way through validation and into the ledger.
        assert response.status_code == 400, response.text
        assert "cannot debit" in response.json()["detail"].lower()


class TestQuotePrecision:
    """
    zar_send_amount accepted any number of decimal places. The limit check
    ran against the value as submitted while the row stored _fiat() of it,
    so "1000.999" was checked against the sender's headroom as 1000.999 and
    then charged as 1001.00 — two different numbers for one request.
    """

    def test_more_than_two_decimal_places_is_refused(
        self, client, approved_auth_headers, beneficiary_factory, user_factory
    ):
        headers, sender = approved_auth_headers
        user_factory(email="precision.recipient@example.com")
        beneficiary = beneficiary_factory(
            sender, contact="precision.recipient@example.com"
        )

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.999",
            },
            headers=headers,
        )
        assert response.status_code == 422, response.text

    def test_the_charged_amount_equals_the_submitted_amount(self, quote_factory):
        quote, _, _, _ = quote_factory(amount="1000.50")
        assert Decimal(quote["zar_send_amount"]) == Decimal("1000.50")


# ---------------------------------------------------------------------------
# Cash-out idempotency
# ---------------------------------------------------------------------------
class TestCashOutIdempotency:
    """
    POST /wallet/cash-out had no dedupe of any kind: no key, no unique
    constraint, no request hash. A double-clicked button debited the
    balance twice and opened two payouts against it.
    """

    @pytest.fixture()
    def funded(self, db_session, approved_user_factory, auth_header_for):
        user = approved_user_factory(email="idempotent@example.com")
        ledger = Ledger(db_session)
        ledger.credit(ledger.wallet_for(user), "UCTUSD", Decimal("100.000000"))
        db_session.commit()
        return auth_header_for(user), user

    def _request(self, client, headers, key=None):
        return client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10.000000", "payout_currency": "USD"},
            headers={**headers, **({"Idempotency-Key": key} if key else {})},
        )

    def test_replaying_a_key_returns_the_original_and_debits_once(
        self, client, db_session, funded
    ):
        headers, user = funded

        first = self._request(client, headers, key="abc-123")
        second = self._request(client, headers, key="abc-123")

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["id"] == second.json()["id"]

        ledger = Ledger(db_session)
        wallet = ledger.wallet_for(user)
        # 100 - 10, not 100 - 20.
        assert ledger.balance(wallet, "UCTUSD") == Decimal("90.000000")

    def test_a_different_key_is_a_genuinely_new_payout(
        self, client, db_session, funded
    ):
        headers, user = funded

        first = self._request(client, headers, key="key-one")
        second = self._request(client, headers, key="key-two")

        assert first.json()["id"] != second.json()["id"]
        ledger = Ledger(db_session)
        assert ledger.balance(
            ledger.wallet_for(user), "UCTUSD"
        ) == Decimal("80.000000")

    def test_requests_without_a_key_behave_as_before(
        self, client, db_session, funded
    ):
        """
        The header is optional, and NULLs are distinct under the unique
        index — so a client that does not send one is unaffected.
        """
        headers, user = funded

        first = self._request(client, headers)
        second = self._request(client, headers)

        assert first.json()["id"] != second.json()["id"]
        ledger = Ledger(db_session)
        assert ledger.balance(
            ledger.wallet_for(user), "UCTUSD"
        ) == Decimal("80.000000")


# ---------------------------------------------------------------------------
# The failed-settlement dead end
# ---------------------------------------------------------------------------
class TestFailedRemittanceRecovery:
    """
    FAILED was an absorbing state holding the sender's money: the admin
    retry endpoint refused it, no refund existed, and limits_service handed
    the headroom back as though nothing had been taken.
    """

    @pytest.fixture()
    def failed_remittance(self, db_session, client, quote_factory, fake_queue):
        from datetime import datetime, timezone

        quote, headers, sender, _ = quote_factory(amount="1000.00")
        client.post(
            f"/remittances/{quote['remittance_id']}/confirm-cash-in",
            json={"cash_in_method": "agent_cash"},
            headers=headers,
        )
        remittance = db_session.get(Remittance, uuid.UUID(quote["remittance_id"]))
        # What the worker does when the on-chain leg is rejected.
        remittance.status = RemittanceStatus.FAILED
        remittance.cash_in_confirmed_at = datetime.now(timezone.utc).replace(
            tzinfo=None
        )
        db_session.commit()
        return remittance, headers, sender

    def test_failed_still_consumes_limit_headroom(
        self, db_session, failed_remittance
    ):
        """
        It used to not: LIMIT_CONSUMING_STATUSES omitted FAILED on the
        reasoning that "nothing left the sender", which is false — a
        settlement can only fail after cash-in was confirmed.
        """
        remittance, _, sender = failed_remittance
        assert RemittanceStatus.FAILED in LIMIT_CONSUMING_STATUSES

        daily, _monthly = limits_service.sender_totals(db_session, sender.id)
        assert daily == Decimal("1000.00")

    def test_a_failure_before_cash_in_does_not_consume_headroom(
        self, db_session, quote_factory
    ):
        """The one case that must not hold anything: nothing was taken."""
        quote, _headers, sender, _ = quote_factory(amount="1000.00")
        remittance = db_session.get(Remittance, uuid.UUID(quote["remittance_id"]))
        remittance.status = RemittanceStatus.FAILED
        remittance.cash_in_confirmed_at = None
        db_session.commit()

        daily, _monthly = limits_service.sender_totals(db_session, sender.id)
        assert daily == Decimal("0")

    def test_an_admin_can_refund_it(
        self, client, db_session, failed_remittance, admin_headers
    ):
        remittance, _, sender = failed_remittance
        admin, _ = admin_headers

        response = client.post(
            f"/admin/remittances/{remittance.id}/refund", headers=admin
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "refunded"

    def test_the_refund_is_visible_in_the_senders_history(
        self, client, db_session, failed_remittance, admin_headers, auth_header_for
    ):
        """
        Written as a ledger entry before the status moves, so the sender
        sees the money come back rather than a row changing colour.
        """
        remittance, _, sender = failed_remittance
        admin, _ = admin_headers
        client.post(f"/admin/remittances/{remittance.id}/refund", headers=admin)

        rows = client.get(
            "/wallet/transactions", headers=auth_header_for(sender)
        ).json()
        refunds = [
            row
            for row in rows
            if row["direction"] == "incoming" and row["currency"] == "ZAR"
        ]
        assert len(refunds) == 1
        assert Decimal(refunds[0]["amount"]) == Decimal("1000.00")

    def test_refunding_releases_the_limit_headroom(
        self, client, db_session, failed_remittance, admin_headers
    ):
        remittance, _, sender = failed_remittance
        admin, _ = admin_headers
        client.post(f"/admin/remittances/{remittance.id}/refund", headers=admin)

        db_session.expire_all()
        daily, _monthly = limits_service.sender_totals(db_session, sender.id)
        assert daily == Decimal("0")
        assert RemittanceStatus.REFUNDED not in LIMIT_CONSUMING_STATUSES

    def test_refunding_twice_is_refused(
        self, client, failed_remittance, admin_headers
    ):
        remittance, _, _ = failed_remittance
        admin, _ = admin_headers

        client.post(f"/admin/remittances/{remittance.id}/refund", headers=admin)
        again = client.post(
            f"/admin/remittances/{remittance.id}/refund", headers=admin
        )
        assert again.status_code == 409

    def test_a_settled_remittance_cannot_be_refunded(
        self, client, db_session, failed_remittance, admin_headers
    ):
        remittance, _, _ = failed_remittance
        remittance.status = RemittanceStatus.SETTLED
        db_session.commit()
        admin, _ = admin_headers

        response = client.post(
            f"/admin/remittances/{remittance.id}/refund", headers=admin
        )
        assert response.status_code == 409

    def test_an_admin_can_retry_it_instead(
        self, client, db_session, failed_remittance, admin_headers, fake_queue
    ):
        """
        confirm-payment has always called itself the manual retry, but
        simulate_cash_in refused anything that was not a live quote — so
        the one state a retry exists for was the one it rejected.
        """
        remittance, _, _ = failed_remittance
        admin, _ = admin_headers

        response = client.post(
            f"/admin/remittances/{remittance.id}/confirm-payment", headers=admin
        )
        assert response.status_code == 200, response.text
        assert response.json()["queued"] is True
        assert response.json()["remittance"]["status"] == "queued"

    def test_a_refunded_remittance_cannot_be_retried(
        self, client, failed_remittance, admin_headers, fake_queue
    ):
        remittance, _, _ = failed_remittance
        admin, _ = admin_headers
        client.post(f"/admin/remittances/{remittance.id}/refund", headers=admin)

        retry = client.post(
            f"/admin/remittances/{remittance.id}/confirm-payment", headers=admin
        )
        assert retry.status_code == 409


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------
class TestRecipientMatching:
    """
    Both resolvers looked up a registered User by an exact match on
    Beneficiary.contact, while the field was documented as "mobile number
    or email" and validated as any string.
    """

    def test_a_phone_number_is_refused_at_the_schema(
        self, client, approved_auth_headers
    ):
        headers, _ = approved_auth_headers
        response = client.post(
            "/beneficiaries/",
            json={
                "full_name": "Phone Recipient",
                "contact": "+27821234567",
                "country": "South Africa",
                "preferred_payout_currency": "ZAR",
                "relationship_to_sender": "Brother",
            },
            headers=headers,
        )
        # It used to save happily and then be unpayable for ever.
        assert response.status_code == 422, response.text

    def test_the_contact_is_stored_lowercase(
        self, client, approved_auth_headers
    ):
        headers, _ = approved_auth_headers
        response = client.post(
            "/beneficiaries/",
            json={
                "full_name": "Mixed Case",
                "contact": "Alice.Smith@Example.COM",
                "country": "United States",
                "preferred_payout_currency": "USD",
                "relationship_to_sender": "Sister",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["contact"] == "alice.smith@example.com"

    def test_a_recipient_registered_in_another_case_still_resolves(
        self, db_session, quote_factory, user_factory
    ):
        """
        "Alice@x.com" and "alice@x.com" are one person. An exact match made
        that an unexplained 409 at cash-in, after the sender had been quoted.
        """
        user_factory(email="MixedCase.Recipient@Example.com")
        quote, _headers, _sender, _beneficiary = quote_factory(
            amount="1000.00",
            register_recipient=False,
            contact="mixedcase.recipient@example.com",
        )
        remittance = db_session.get(Remittance, uuid.UUID(quote["remittance_id"]))

        resolved = cashin_cashout_service.assert_recipient_registered(
            db_session, remittance
        )
        assert resolved.email == "MixedCase.Recipient@Example.com"

    def test_an_unpriceable_payout_currency_is_no_longer_offered(
        self, client, approved_auth_headers
    ):
        """
        EUR saved fine, then the quote silently repriced in USD and
        cash-out refused it outright.
        """
        headers, _ = approved_auth_headers
        response = client.post(
            "/beneficiaries/",
            json={
                "full_name": "Euro Recipient",
                "contact": "euro@example.com",
                "country": "Germany",
                "preferred_payout_currency": "EUR",
                "relationship_to_sender": "Friend",
            },
            headers=headers,
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Authorization and auth surface
# ---------------------------------------------------------------------------
class TestReadAccess:
    def test_history_is_readable_without_approved_kyc(
        self, client, auth_headers
    ):
        """
        Sending needs approved KYC; reading what you already sent does not.
        Gating it locked a rejected sender out of their own records — and
        made the frontend's default tab 403 on every mount.
        """
        headers, _ = auth_headers
        assert client.get("/remittances/", headers=headers).status_code == 200

    def test_history_is_readable_after_kyc_is_rejected(
        self, client, db_session, approved_auth_headers, quote_factory
    ):
        quote, headers, sender, _ = quote_factory(amount="1000.00")
        sender.kyc_status = KYCStatus.REJECTED
        db_session.commit()

        listing = client.get("/remittances/", headers=headers)
        detail = client.get(
            f"/remittances/{quote['remittance_id']}", headers=headers
        )
        assert listing.status_code == 200
        assert detail.status_code == 200

    def test_ownership_is_still_enforced(
        self, client, quote_factory, approved_user_factory, auth_header_for
    ):
        """Dropping the KYC gate must not have dropped the ownership check."""
        quote, _headers, _sender, _ = quote_factory(amount="1000.00")
        intruder = approved_user_factory(email="intruder@example.com")

        response = client.get(
            f"/remittances/{quote['remittance_id']}",
            headers=auth_header_for(intruder),
        )
        assert response.status_code == 404


class TestKycBinding:
    def test_an_application_must_carry_the_callers_own_email(
        self, client, auth_headers
    ):
        """
        Nothing checked that the identity on the application described the
        account submitting it, so an account could be verified against
        someone else's documents.
        """
        headers, _ = auth_headers
        response = client.post(
            "/kyc/apply",
            json={
                "full_name": "Someone Else",
                "date_of_birth": "1990-01-01",
                "nationality": "South African",
                "identification_number": "9001015800086",
                "residential_address": "1 Long Street, Cape Town",
                "mobile_number": "+27821234567",
                "email": "someone.else@example.com",
                "source_of_funds": "Salary",
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert "registered under" in response.json()["detail"]

    def test_the_callers_own_email_is_accepted(self, client, auth_headers):
        headers, user = auth_headers
        response = client.post(
            "/kyc/apply",
            json={
                "full_name": "Test User",
                "date_of_birth": "1990-01-01",
                "nationality": "South African",
                "identification_number": "9001015800086",
                "residential_address": "1 Long Street, Cape Town",
                "mobile_number": "+27821234567",
                "email": user.email.upper(),  # case must not matter
                "source_of_funds": "Salary",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text


class TestPasswordLimits:
    def test_a_password_longer_than_bcrypt_hashes_is_refused(self, client):
        """
        bcrypt reads at most 72 bytes. At max_length=128 a 100-character
        passphrase was accepted and silently truncated, so the user could
        log in with only its first 72 characters and was never told.
        """
        response = client.post(
            "/auth/register",
            json={
                "email": "long@example.com",
                "password": "a" * 100,
                "full_name": "Long Password",
            },
        )
        assert response.status_code == 422

    def test_a_multibyte_password_is_measured_in_bytes(self, client):
        # 40 three-byte characters = 120 bytes, but only 40 characters.
        response = client.post(
            "/auth/register",
            json={
                "email": "multibyte@example.com",
                "password": "é" * 40 + "中" * 20,
                "full_name": "Multibyte",
            },
        )
        assert response.status_code == 422

    def test_a_password_at_the_limit_still_works(self, client):
        response = client.post(
            "/auth/register",
            json={
                "email": "exactly@example.com",
                "password": "a" * 72,
                "full_name": "Exactly Seventy Two",
            },
        )
        assert response.status_code == 201, response.text


class TestProfileSurface:
    def test_me_reports_admin_status(self, client, admin_headers, auth_headers):
        """
        The frontend used to infer this by calling the admin KYC queue and
        reading a 403 as "no" — fetching the whole table as a permission
        probe on every page load.
        """
        admin, _ = admin_headers
        plain, _ = auth_headers
        assert client.get("/auth/me", headers=admin).json()["is_admin"] is True
        assert client.get("/auth/me", headers=plain).json()["is_admin"] is False

    def test_limits_are_decimal_strings_not_floats(self, client, auth_headers):
        headers, _ = auth_headers
        limits = client.get("/auth/me", headers=headers).json()["limits"]
        assert isinstance(limits["daily_limit_zar"], str)
        assert isinstance(limits["monthly_limit_zar"], str)


class TestTimestampsAreExplicitlyUtc:
    """
    Every DateTime column is naive, and both SQLite and Postgres return it
    that way. Serialised bare, "2026-09-24T14:32:00" was read by the
    browser as local time — putting a fifteen-minute quote nearly two hours
    in the past for a UTC+2 user.
    """

    def test_a_quote_expiry_carries_a_utc_marker(self, quote_factory):
        quote, _, _, _ = quote_factory(amount="1000.00")
        expiry = quote["quote_expires_at"]
        assert expiry.endswith("Z") or expiry.endswith("+00:00"), expiry

    def test_history_timestamps_carry_a_utc_marker(
        self, client, quote_factory
    ):
        _quote, headers, _sender, _ = quote_factory(amount="1000.00")
        row = client.get("/remittances/", headers=headers).json()[0]
        assert row["created_at"].endswith("Z") or row["created_at"].endswith(
            "+00:00"
        )


# ---------------------------------------------------------------------------
# Ledger invariants
# ---------------------------------------------------------------------------
class TestDoubleCreditBackstop:
    """
    uq_wallet_tx_remittance_direction_status is documented as the thing
    standing between a bypassed status compare-and-swap and free money.
    Nothing had ever asserted that it fires — drop it in a bad migration
    and every other test still passed.
    """

    def test_a_second_successful_credit_for_one_remittance_is_refused(
        self, db_session, remittance_factory
    ):
        remittance, _sender, recipient = remittance_factory()
        ledger = Ledger(db_session)
        wallet = ledger.wallet_for(recipient)

        ledger.credit(
            wallet, "UCTUSD", Decimal("10.000000"), remittance_id=remittance.id
        )
        db_session.commit()

        # The violation surfaces on Ledger's own flush, before the caller
        # ever reaches a commit -- which is the point: the constraint stops
        # the write rather than letting a second credit sit in the session.
        with pytest.raises(IntegrityError):
            ledger.credit(
                wallet,
                "UCTUSD",
                Decimal("10.000000"),
                remittance_id=remittance.id,
            )
        db_session.rollback()

    def test_a_failure_then_a_success_is_still_representable(
        self, db_session, remittance_factory
    ):
        """
        status is part of the key precisely so a recorded failure followed
        by a successful retry is allowed. Only two successes collide.
        """
        remittance, _sender, recipient = remittance_factory()
        ledger = Ledger(db_session)
        wallet = ledger.wallet_for(recipient)

        ledger.record_external(
            wallet,
            Direction.INCOMING,
            "UCTUSD",
            Decimal("10.000000"),
            remittance_id=remittance.id,
            status=EntryStatus.FAILED,
            failure_reason="tecPATH_DRY",
        )
        db_session.commit()

        ledger.credit(
            wallet, "UCTUSD", Decimal("10.000000"), remittance_id=remittance.id
        )
        db_session.commit()

        assert ledger.balance(wallet, "UCTUSD") == Decimal("10.000000")


class TestExchangeRateOutage:
    """
    The /quote path returns 503 when no rate can be produced, and was
    tested. The identical branch on /wallet/cash-out was not.
    """

    def test_cash_out_returns_503_when_no_rate_is_available(
        self, client, auth_headers, monkeypatch
    ):
        def unavailable(*_args, **_kwargs):
            raise fx_rate_service.FxRateUnavailableError("provider is down")

        monkeypatch.setattr(fx_rate_service, "get_usd_zar_rate", unavailable)

        headers, _ = auth_headers
        response = client.post(
            "/wallet/cash-out",
            json={"uctusd_amount": "10.000000", "payout_currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 503
        assert "Exchange rate unavailable" in response.json()["detail"]


class TestInsertRaces:
    """
    Ledger.wallet_for and _locked_balance_row were both SELECT-then-INSERT
    against a unique constraint. Two concurrent first-time requests for the
    same user each saw nothing, each inserted, and the loser got an
    IntegrityError out as a 500.

    A genuine race is not reproducible against a single in-memory
    connection, so these drive the recovery path directly: the row already
    exists by the time the insert runs, which is exactly the state the
    loser of the race finds itself in.
    """

    def test_a_losing_wallet_insert_reads_the_winners_row(
        self, db_session, user_factory
    ):
        from app.models.wallet import Wallet

        user = user_factory(email="race@example.com")
        winner = Wallet(user_id=user.id)
        db_session.add(winner)
        db_session.commit()

        ledger = Ledger(db_session)
        recovered = ledger._insert_or_reselect(
            Wallet(user_id=user.id),
            lambda: db_session.query(Wallet).filter(Wallet.user_id == user.id),
        )

        assert recovered.id == winner.id

    def test_a_losing_balance_insert_reads_the_winners_row(
        self, db_session, user_factory
    ):
        from app.models.wallet import LedgerBalance

        user = user_factory(email="race2@example.com")
        ledger = Ledger(db_session)
        wallet = ledger.wallet_for(user)
        db_session.commit()

        winner = LedgerBalance(
            wallet_id=wallet.id, currency="UCTUSD", amount=Decimal("0")
        )
        db_session.add(winner)
        db_session.commit()

        recovered = ledger._insert_or_reselect(
            LedgerBalance(
                wallet_id=wallet.id, currency="UCTUSD", amount=Decimal("0")
            ),
            lambda: db_session.query(LedgerBalance).filter(
                LedgerBalance.wallet_id == wallet.id,
                LedgerBalance.currency == "UCTUSD",
            ),
        )

        assert recovered.id == winner.id

    def test_an_unrelated_integrity_error_still_raises(
        self, db_session, user_factory
    ):
        """
        The recovery must be for the race and nothing else — a failure
        with no row to re-read is a real error and has to surface.
        """
        from app.models.wallet import Wallet

        user = user_factory(email="race3@example.com")
        ledger = Ledger(db_session)

        with pytest.raises(IntegrityError):
            ledger._insert_or_reselect(
                # No user_id at all: NOT NULL, and nothing to re-select.
                Wallet(user_id=None),
                lambda: db_session.query(Wallet).filter(
                    Wallet.user_id == user.id
                ),
            )
        db_session.rollback()


class TestLimitRaceRecheck:
    """
    The quote path checks the limit, inserts, then checks again with the
    new row counted, and rolls back if the second check fails.

    Two concurrent quotes used to read the same running total, both pass,
    and both commit. The row lock alone does not fix that on SQLite, where
    SELECT ... FOR UPDATE is a no-op -- measured against the shipped
    configuration, ten parallel R1 000 quotes put R6 000 through an R3 000
    limit. The re-check is what holds on both databases; with it, the same
    ten requests create exactly three.

    A real race needs more than one connection, which the in-memory test
    database does not have, so this drives the branch directly: the first
    check passes and the second does not, which is exactly the state the
    loser of a race finds itself in.
    """

    def test_a_quote_that_loses_the_race_is_rolled_back(
        self, client, db_session, approved_auth_headers, beneficiary_factory,
        user_factory, monkeypatch
    ):
        headers, sender = approved_auth_headers
        user_factory(email="race.recipient@example.com")
        beneficiary = beneficiary_factory(
            sender, contact="race.recipient@example.com"
        )

        real = limits_service.assert_within_limits
        calls = {"n": 0}

        def pass_then_fail(db, user, amount, now=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return real(db, user, amount, now)
            # A competing request committed in between.
            raise limits_service.LimitExceededError(
                "daily", amount, Decimal("0"), Decimal("3000")
            )

        monkeypatch.setattr(
            "app.routers.remittances.assert_within_limits", pass_then_fail
        )

        response = client.post(
            "/remittances/quote",
            json={
                "beneficiary_id": str(beneficiary.id),
                "zar_send_amount": "1000.00",
            },
            headers=headers,
        )

        assert response.status_code == 403, response.text
        assert calls["n"] == 2, "the second check did not run"

        # The critical part: the losing quote must not be left behind
        # holding headroom it was refused.
        remaining = (
            db_session.query(Remittance)
            .filter(Remittance.sender_id == sender.id)
            .count()
        )
        assert remaining == 0

    def test_the_headroom_reported_already_counts_this_quote(
        self, quote_factory
    ):
        """
        The response's remaining headroom is computed from totals taken
        after the insert, so it must not subtract the amount a second
        time.
        """
        quote, _headers, _sender, _ = quote_factory(amount="1000.00")
        limits = quote["limits"]
        assert Decimal(limits["daily_limit_zar"]) == Decimal("3000")
        assert Decimal(limits["daily_remaining_zar"]) == Decimal("2000")
