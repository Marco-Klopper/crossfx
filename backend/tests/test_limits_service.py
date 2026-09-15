"""
Tests for remittance limit enforcement (spec §6).

Two things are being pinned here, because neither is obvious from the
code alone: the window is a South African calendar day/month, and only
remittances that are actually on their way — or quotes that are still
live — consume headroom.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.config import settings
from app.models.beneficiary import Beneficiary
from app.models.remittance import Remittance, RemittanceStatus
from app.models.user import KYCStatus
from app.services.limits_service import (
    LimitExceededError,
    assert_within_limits,
    limits_for,
    sender_totals,
    window_starts,
)

# 10:00 on 15 September, South African time.
NOW = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def default_limits(monkeypatch):
    monkeypatch.setattr(settings, "verified_daily_limit", 3000)
    monkeypatch.setattr(settings, "verified_monthly_limit", 25000)
    monkeypatch.setattr(settings, "unverified_daily_limit", 0)
    monkeypatch.setattr(settings, "unverified_monthly_limit", 0)


@pytest.fixture()
def sender(db_session, user_factory):
    user = user_factory(email="limits.sender@example.com")
    user.kyc_status = KYCStatus.APPROVED
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def make_remittance(db_session, sender):
    """
    Writes a remittance row directly, so a test can place one at an exact
    instant — which is the only way to test a window boundary.
    """
    beneficiary = Beneficiary(
        sender_id=sender.id,
        full_name="Recipient",
        contact="limits.recipient@example.com",
        country="US",
        preferred_payout_currency="USD",
        relationship_to_sender="family",
    )
    db_session.add(beneficiary)
    db_session.commit()

    def _make(
        amount="1000.00",
        created_at=NOW,
        status=RemittanceStatus.SETTLED,
        quote_expires_at=None,
    ):
        remittance = Remittance(
            idempotency_key=uuid.uuid4().hex,
            sender_id=sender.id,
            beneficiary_id=beneficiary.id,
            zar_send_amount=Decimal(amount),
            fx_rate_used=Decimal("18.500000"),
            transaction_fee_zar=Decimal("40.00"),
            fx_margin_zar=Decimal("10.00"),
            net_converted_zar=Decimal(amount) - Decimal("50.00"),
            uctusd_amount=Decimal("51.351351"),
            status=status,
            # The columns are naive UTC (SQLAlchemy's portable default).
            created_at=created_at.astimezone(timezone.utc).replace(tzinfo=None),
            quote_expires_at=(
                quote_expires_at.astimezone(timezone.utc).replace(tzinfo=None)
                if quote_expires_at is not None
                else None
            ),
        )
        db_session.add(remittance)
        db_session.commit()
        return remittance

    return _make


class TestWindow:
    def test_day_and_month_start_at_midnight_south_african_time(self):
        day_start, month_start = window_starts(NOW)

        # 00:00 SAST on 15 Sep is 22:00 UTC on 14 Sep.
        assert day_start == datetime(2026, 9, 14, 22, 0)
        assert month_start == datetime(2026, 8, 31, 22, 0)

    def test_a_send_after_midnight_sast_counts_toward_the_new_day(
        self, db_session, sender, make_remittance
    ):
        """
        The reason the window is local rather than UTC. 00:30 SAST is
        still 22:30 UTC the previous day; counting it against yesterday
        would reset a sender's daily limit at 02:00 their time.
        """
        make_remittance(
            created_at=datetime(2026, 9, 14, 22, 30, tzinfo=timezone.utc)
        )

        daily, monthly = sender_totals(db_session, sender.id, NOW)
        assert daily == Decimal("1000.00")
        assert monthly == Decimal("1000.00")

    def test_a_send_before_midnight_sast_counts_only_toward_the_month(
        self, db_session, sender, make_remittance
    ):
        make_remittance(
            created_at=datetime(2026, 9, 14, 21, 30, tzinfo=timezone.utc)
        )

        daily, monthly = sender_totals(db_session, sender.id, NOW)
        assert daily == Decimal("0")
        assert monthly == Decimal("1000.00")

    def test_last_month_counts_toward_neither(
        self, db_session, sender, make_remittance
    ):
        make_remittance(
            created_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        )

        assert sender_totals(db_session, sender.id, NOW) == (
            Decimal("0"),
            Decimal("0"),
        )


class TestWhichRemittancesCount:
    @pytest.mark.parametrize(
        "status",
        [
            RemittanceStatus.QUOTED,
            RemittanceStatus.CASH_IN_CONFIRMED,
            RemittanceStatus.QUEUED,
            RemittanceStatus.SETTLING,
            RemittanceStatus.SETTLED,
        ],
    )
    def test_everything_in_flight_or_settled_consumes_headroom(
        self, db_session, sender, make_remittance, status
    ):
        make_remittance(status=status)
        daily, _ = sender_totals(db_session, sender.id, NOW)
        assert daily == Decimal("1000.00")

    def test_a_failed_remittance_gives_its_headroom_back(
        self, db_session, sender, make_remittance
    ):
        """No value left the sender, so it cannot count against them."""
        make_remittance(status=RemittanceStatus.FAILED)
        assert sender_totals(db_session, sender.id, NOW) == (
            Decimal("0"),
            Decimal("0"),
        )

    def test_a_live_quote_holds_headroom(
        self, db_session, sender, make_remittance
    ):
        """
        Otherwise a sender could take out ten quotes for their full limit
        and fund all ten.
        """
        make_remittance(
            status=RemittanceStatus.QUOTED,
            quote_expires_at=datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc),
        )
        daily, _ = sender_totals(db_session, sender.id, NOW)
        assert daily == Decimal("1000.00")

    def test_an_expired_quote_releases_it(
        self, db_session, sender, make_remittance
    ):
        make_remittance(
            status=RemittanceStatus.QUOTED,
            quote_expires_at=datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc),
        )
        assert sender_totals(db_session, sender.id, NOW) == (
            Decimal("0"),
            Decimal("0"),
        )

    def test_another_senders_remittances_are_not_counted(
        self, db_session, sender, make_remittance, user_factory
    ):
        make_remittance()
        other = user_factory(email="someone.else@example.com")

        assert sender_totals(db_session, other.id, NOW) == (
            Decimal("0"),
            Decimal("0"),
        )


class TestLimitsFor:
    def test_approved_kyc_unlocks_the_verified_limits(self):
        assert limits_for(KYCStatus.APPROVED) == (
            Decimal("3000"),
            Decimal("25000"),
        )

    @pytest.mark.parametrize(
        "status",
        [KYCStatus.NOT_STARTED, KYCStatus.PENDING, KYCStatus.REJECTED],
    )
    def test_everything_else_gets_the_unverified_limits(self, status):
        assert limits_for(status) == (Decimal("0"), Decimal("0"))


class TestAssertWithinLimits:
    def test_returns_usage_when_the_send_fits(self, db_session, sender):
        usage = assert_within_limits(db_session, sender, Decimal("500"), NOW)

        assert usage.daily_remaining == Decimal("3000")
        assert usage.monthly_remaining == Decimal("25000")

    def test_raises_on_the_daily_limit(
        self, db_session, sender, make_remittance
    ):
        make_remittance(amount="2500.00")

        with pytest.raises(LimitExceededError) as exc_info:
            assert_within_limits(db_session, sender, Decimal("1000"), NOW)

        assert exc_info.value.period == "daily"
        assert exc_info.value.remaining == Decimal("500.00")

    def test_raises_on_the_monthly_limit(
        self, db_session, sender, make_remittance
    ):
        for day in range(1, 11):
            make_remittance(
                amount="2500.00",
                created_at=datetime(2026, 9, day, 10, 0, tzinfo=timezone.utc),
            )

        with pytest.raises(LimitExceededError) as exc_info:
            assert_within_limits(db_session, sender, Decimal("1000"), NOW)

        assert exc_info.value.period == "monthly"

    def test_names_the_daily_limit_when_both_are_breached(
        self, db_session, sender, make_remittance
    ):
        """
        The daily limit is the tighter one, so it is the one worth telling
        the sender about — "come back tomorrow" is actionable in a way
        that "come back next month" is not.
        """
        for day in range(1, 10):
            make_remittance(
                amount="2500.00",
                created_at=datetime(2026, 9, day, 10, 0, tzinfo=timezone.utc),
            )
        make_remittance(amount="2900.00")

        with pytest.raises(LimitExceededError) as exc_info:
            assert_within_limits(db_session, sender, Decimal("2000"), NOW)

        assert exc_info.value.period == "daily"
