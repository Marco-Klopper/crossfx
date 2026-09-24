"""
Shared field types for the response schemas.
"""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def _as_utc(value: datetime) -> datetime:
    """Tags a naive timestamp as UTC, and normalises an aware one to it."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Every DateTime column in app/models is timezone-naive, and both SQLite
# and Postgres hand the value back without tzinfo even though it was
# written as aware UTC (_utcnow). Serialised as-is, a quote expiry left
# the API as "2026-09-24T14:32:00" — no Z, no offset — and `new Date(...)`
# in the browser read that as *local* time. For a UTC+2 user a
# fifteen-minute quote therefore arrived having apparently expired an
# hour and three-quarters ago.
#
# Fixing it at the column would mean retyping fifteen columns across five
# tables; fixing it here costs one annotation per field and makes every
# timestamp the API emits explicitly UTC. Switching the columns to
# DateTime(timezone=True) is still worth doing, and is recorded as a
# follow-up in the tech spec's §15.
UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
