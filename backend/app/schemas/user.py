"""
Request/response shapes for auth.py and the /me profile endpoint.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import KYCStatus
from app.schemas.common import UtcDatetime


# bcrypt hashes at most the first 72 bytes of a password and silently
# discards the rest. At max_length=128 that meant someone who set a
# 100-character passphrase could log in with only its first 72
# characters, and would never be told. Refusing the password is honest;
# accepting it and quietly ignoring a quarter of it is not.
MAX_PASSWORD_BYTES = 72


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)
    full_name: str = Field(min_length=1, max_length=200)

    @field_validator("password")
    @classmethod
    def within_bcrypt_limit(cls, value: str) -> str:
        # max_length counts characters; bcrypt's limit is bytes, so a
        # passphrase with any non-ASCII in it can pass the first check
        # and still be truncated.
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"password must be at most {MAX_PASSWORD_BYTES} bytes "
                f"(bcrypt ignores anything beyond that)"
            )
        return value


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    kyc_status: KYCStatus
    is_admin: bool
    created_at: UtcDatetime


class TransactionLimits(BaseModel):
    """The daily/monthly ZAR sending limits that apply for a given kyc_status."""

    # Decimal, not float. These were the only float money anywhere in
    # the API: /auth/me returned 3000.0 where every other endpoint
    # returns a Decimal string, which is exactly the shape that invites
    # a client to do float arithmetic on a rand amount.
    daily_limit_zar: Decimal
    monthly_limit_zar: Decimal


class MeResponse(BaseModel):
    """
    GET /auth/me. Deliberately does NOT include a wallet balance summary — the
    original stub's TODO asked for one, but wallets belong to Track 2
    (app.models.wallet), which doesn't exist behind an endpoint yet. Adding a
    fake zero here would be worse than omitting the field; Track 2 should add
    it once app.services (wallet balance lookup) exists.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    kyc_status: KYCStatus
    # The frontend used to work out admin-ness by calling
    # GET /admin/kyc/applications and treating a 403 as "no" - which
    # fetched the entire KYC table as a permission probe on every page
    # load, and hid the Admin tab on any transient network error.
    is_admin: bool
    limits: TransactionLimits
