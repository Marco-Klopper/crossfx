"""
Remittance limit enforcement (spec §6).

`fee_service.check_within_limits` answers "does this amount fit?" given a
sender's running totals. This module answers the harder question it
deliberately leaves out: *which* remittances count, and over what window.

Two decisions live here, because nothing else in the codebase makes them:

  Window.  Calendar day and calendar month in South African time. The
  limits are a South African regulatory construct denominated in rand, so
  a sender's "today" is their today, not UTC's — otherwise the daily
  limit would reset at 02:00 local, mid-evening for anyone sending after
  work.

  Which rows.  Everything that is either settled or still on its way
  there, plus quotes that are still live. A FAILED remittance frees its
  headroom (no value left the sender), and so does an expired quote (they
  never funded it). That is the honest reading of "how much have you sent
  this month", and it is the one that cannot be gamed by requesting a
  hundred quotes.

South African time is expressed as a fixed +02:00 offset rather than the
Africa/Johannesburg zone: SAST has never observed DST, and a fixed offset
needs no `tzdata` package on the Windows machines the team develops on.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.remittance import (
    LIMIT_CONSUMING_STATUSES,
    Remittance,
    RemittanceStatus,
)
from app.models.user import KYCStatus, User

SAST = timezone(timedelta(hours=2), name="SAST")


class LimitExceededError(Exception):
    """
    A send that would breach a limit. Carries the numbers so the API can
    tell the sender how much room they actually have rather than just
    refusing.
    """

    def __init__(self, period: str, attempted: Decimal, remaining: Decimal, limit: Decimal) -> None:
        self.period = period
        self.attempted = attempted
        self.remaining = remaining
        self.limit = limit
        super().__init__(
            f"R{attempted} exceeds the {period} limit of R{limit}: "
            f"R{remaining} remaining"
        )


@dataclass
class LimitUsage:
    """A sender's limit position, as the quote screen should show it."""

    daily_total: Decimal
    monthly_total: Decimal
    daily_limit: Decimal
    monthly_limit: Decimal

    @property
    def daily_remaining(self) -> Decimal:
        return max(self.daily_limit - self.daily_total, Decimal("0"))

    @property
    def monthly_remaining(self) -> Decimal:
        return max(self.monthly_limit - self.monthly_total, Decimal("0"))


def _as_naive_utc(moment: datetime) -> datetime:
    """
    The DateTime columns are timezone-naive and hold UTC (SQLAlchemy's
    portable default, and what SQLite can store), so every value used in
    a WHERE clause has to be reduced the same way or the comparison is
    meaningless.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def window_starts(now: datetime | None = None) -> tuple[datetime, datetime]:
    """
    (start of today, start of this month) in SAST, returned as naive UTC
    for use against the stored timestamps.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    local = now.astimezone(SAST)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    return _as_naive_utc(day_start), _as_naive_utc(month_start)


def limits_for(kyc_status: KYCStatus) -> tuple[Decimal, Decimal]:
    """
    (daily, monthly) ZAR limits for a KYC status. Unverified senders get
    zero by default, which is the point: /auth/me already advertises this
    pair, and a quote must agree with what the profile screen promised.
    """
    verified = kyc_status == KYCStatus.APPROVED
    daily = (
        settings.verified_daily_limit
        if verified
        else settings.unverified_daily_limit
    )
    monthly = (
        settings.verified_monthly_limit
        if verified
        else settings.unverified_monthly_limit
    )
    return Decimal(str(daily)), Decimal(str(monthly))


def sender_totals(
    db: Session, sender_id: uuid.UUID, now: datetime | None = None
) -> tuple[Decimal, Decimal]:
    """(today's total, this month's total) in ZAR for one sender."""
    now = now or datetime.now(timezone.utc)
    day_start, month_start = window_starts(now)
    now_naive = _as_naive_utc(now)

    def _total(since: datetime) -> Decimal:
        total = (
            db.query(func.coalesce(func.sum(Remittance.zar_send_amount), 0))
            .filter(
                Remittance.sender_id == sender_id,
                Remittance.status.in_(LIMIT_CONSUMING_STATUSES),
                Remittance.created_at >= since,
                # A live quote holds headroom; an expired one does not.
                # Rows with no expiry (already funded) are unaffected.
                or_(
                    Remittance.status != RemittanceStatus.QUOTED,
                    Remittance.quote_expires_at.is_(None),
                    Remittance.quote_expires_at > now_naive,
                ),
                # A failed settlement keeps holding headroom, because the
                # sender's rand is still gone until someone refunds it
                # (REFUNDED is deliberately absent from
                # LIMIT_CONSUMING_STATUSES). The exception is a row that
                # failed before cash-in was ever confirmed: nothing left
                # the sender there, so it must not hold anything.
                or_(
                    Remittance.status != RemittanceStatus.FAILED,
                    Remittance.cash_in_confirmed_at.isnot(None),
                ),
            )
            .scalar()
        )
        return Decimal(total or 0)

    return _total(day_start), _total(month_start)


def usage_for(
    db: Session, user: User, now: datetime | None = None
) -> LimitUsage:
    daily_total, monthly_total = sender_totals(db, user.id, now)
    daily_limit, monthly_limit = limits_for(user.kyc_status)
    return LimitUsage(
        daily_total=daily_total,
        monthly_total=monthly_total,
        daily_limit=daily_limit,
        monthly_limit=monthly_limit,
    )


def assert_within_limits(
    db: Session,
    user: User,
    amount: Decimal,
    now: datetime | None = None,
) -> LimitUsage:
    """
    Checks one more send of `amount` against the sender's limits, raising
    LimitExceededError if it does not fit. Returns the usage either way,
    so the caller can put the remaining headroom on the quote.

    The daily check runs first: it is the tighter of the two, and naming
    the limit the sender actually hit is more useful than naming whichever
    one the query happened to evaluate first.
    """
    usage = usage_for(db, user, now)
    if usage.daily_total + amount > usage.daily_limit:
        raise LimitExceededError(
            "daily", amount, usage.daily_remaining, usage.daily_limit
        )
    if usage.monthly_total + amount > usage.monthly_limit:
        raise LimitExceededError(
            "monthly", amount, usage.monthly_remaining, usage.monthly_limit
        )
    return usage
