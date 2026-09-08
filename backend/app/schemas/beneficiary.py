"""
Request/response shapes for routers/beneficiaries.py.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, ConfigDict

# Small allowlist rather than a free-text field: the brief scopes this prototype
# to UCTUSD-denominated payouts and a couple of familiar fiat currencies for the
# simulated cash-out. Extend here if Track 3's cash-out flow needs more.
ALLOWED_PAYOUT_CURRENCIES = {"UCTUSD", "USD", "ZAR", "EUR", "GBP"}


class BeneficiaryCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    contact: str = Field(min_length=1, max_length=200)  # mobile number or email
    country: str = Field(min_length=1, max_length=100)
    preferred_payout_currency: str
    relationship_to_sender: str = Field(min_length=1, max_length=100)

    @field_validator("preferred_payout_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in ALLOWED_PAYOUT_CURRENCIES:
            raise ValueError(
                f"preferred_payout_currency must be one of {sorted(ALLOWED_PAYOUT_CURRENCIES)}"
            )
        return normalized


class BeneficiaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    contact: str
    country: str
    preferred_payout_currency: str
    relationship_to_sender: str
    created_at: datetime
