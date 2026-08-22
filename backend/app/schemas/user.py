"""
Request/response shapes for auth.py and the /me profile endpoint.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import KYCStatus


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


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
    created_at: datetime


class TransactionLimits(BaseModel):
    """The daily/monthly ZAR sending limits that apply for a given kyc_status."""

    daily_limit_zar: float
    monthly_limit_zar: float


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
    limits: TransactionLimits
