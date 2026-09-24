"""
Request/response shapes for routers/beneficiaries.py.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict

# Small allowlist rather than a free-text field: the brief scopes this
# prototype to UCTUSD-denominated payouts and the fiat the corridor can
# actually price.
#
# EUR and GBP used to be offered here and were a trap: the beneficiary
# saved fine, but POST /remittances/quote silently repriced the payout
# estimate in USD (fee_service.PRICEABLE_PAYOUT_CURRENCIES has no rate
# for them) and POST /wallet/cash-out refused them outright. A sender who
# picked EUR was quoted dollars with no explanation. Offer only what the
# system can honour.
ALLOWED_PAYOUT_CURRENCIES = {"UCTUSD", "USD", "ZAR"}


class BeneficiaryCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    # An email address, not "mobile number or email" as this field was
    # once documented. Settlement resolves the recipient by looking up a
    # registered User with this address
    # (cashin_cashout_service.assert_recipient_registered), so a phone
    # number here produced a beneficiary that could be saved, quoted
    # against, and then never paid -- the sender only found out at
    # cash-in, with a 409. Validating it up front makes the real
    # constraint visible at the point the mistake is made.
    contact: EmailStr = Field(max_length=200)
    country: str = Field(min_length=1, max_length=100)
    preferred_payout_currency: str
    relationship_to_sender: str = Field(min_length=1, max_length=100)

    @field_validator("contact")
    @classmethod
    def normalise_contact(cls, value: str) -> str:
        # Stored lowercase so the lookup at settlement is a plain
        # comparison, and so uq_beneficiary_sender_contact actually
        # catches "Alice@x.com" added twice in different cases.
        return value.strip().lower()

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
