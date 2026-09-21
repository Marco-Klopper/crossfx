"""
A manually pinned exchange rate, for exchange_rate_source="table" (spec §5).

Rows are append-only: a new rate is a new row with a later
`effective_from`, never an UPDATE, so a quote can always be explained
afterwards by the rate that was current when it was issued. Seed one with:

    python -m scripts.seed_fx_rates --rate 18.50
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Numeric, String, Uuid

from app.database import Base


class FxRate(Base):
    __tablename__ = "fx_rates"

    # The only pair the corridor quotes today. Stored rather than implied
    # so a second corridor does not mean a second table.
    USD_ZAR = "USD/ZAR"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    currency_pair = Column(String, nullable=False, index=True)
    # Units of the quote currency per 1 unit of the base — ZAR per USD.
    rate = Column(Numeric(12, 6), nullable=False)

    effective_from = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # Who or what pinned it: "seed_fx_rates", an admin's email, a provider.
    source = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
