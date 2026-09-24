"""
Request/response shapes for kyc.py (submission + status) and admin.py (review).

app.models.kyc documents the ApplicationStatus <-> User.KYCStatus mapping that
the admin approve/reject endpoints maintain; these schemas just carry the data.
"""
import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.kyc import ApplicationStatus
from app.models.user import KYCStatus
from app.schemas.common import UtcDatetime

MIN_APPLICANT_AGE_YEARS = 18


class KYCApplicationCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: date
    nationality: str = Field(min_length=1, max_length=100)
    identification_number: str = Field(min_length=1, max_length=100)
    residential_address: str = Field(min_length=1, max_length=500)
    mobile_number: str = Field(pattern=r"^\+?[0-9 ()-]{7,20}$")
    email: EmailStr
    source_of_funds: str = Field(min_length=1, max_length=500)

    @field_validator("date_of_birth")
    @classmethod
    def must_be_adult_and_past(cls, value: date) -> date:
        today = date.today()
        if value >= today:
            raise ValueError("date_of_birth must be in the past")
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if age < MIN_APPLICANT_AGE_YEARS:
            raise ValueError(f"applicant must be at least {MIN_APPLICANT_AGE_YEARS} years old")
        return value


class KYCApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ApplicationStatus
    submitted_at: UtcDatetime
    reviewed_at: UtcDatetime | None = None
    rejection_reason: str | None = None


class AdminKYCApplicationRead(KYCApplicationRead):
    """GET /admin/kyc/applications — adds the applicant fields an admin needs to review."""

    user_id: uuid.UUID
    full_name: str
    email: EmailStr
    nationality: str
    identification_number: str


class KYCStatusResponse(BaseModel):
    """GET /kyc/status — the user's current status plus their latest application, if any."""

    kyc_status: KYCStatus
    latest_application: KYCApplicationRead | None = None


class KYCReviewRequest(BaseModel):
    """Body for admin.py's reject endpoint. Approve needs no body."""

    reason: str | None = Field(default=None, max_length=500)
