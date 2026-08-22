"""
User account. Passwords are hashed via app.security.hashing — never store plaintext.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, String, Uuid
from sqlalchemy.orm import relationship

from app.database import Base


class KYCStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    kyc_status = Column(Enum(KYCStatus), default=KYCStatus.NOT_STARTED, nullable=False)
    # Grants access to app.dependencies.require_admin-gated routes (routers/admin.py).
    # No API route can set this — see backend/scripts/create_admin.py.
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # foreign_keys pinned to user_id: KYCApplication has a second FK to users.id
    # (reviewed_by_admin_id) that SQLAlchemy can't otherwise disambiguate.
    kyc_applications = relationship(
        "KYCApplication",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="KYCApplication.user_id",
    )
    beneficiaries = relationship(
        "Beneficiary", back_populates="sender", cascade="all, delete-orphan"
    )
    # Wallet (Track 2) and Remittance (Track 3) relationships are that track's to wire
    # up — see docs/work-split.html.
