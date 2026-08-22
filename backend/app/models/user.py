"""
User account. Passwords are hashed via app.security.hashing — never store plaintext.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class KYCStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    kyc_status = Column(Enum(KYCStatus), default=KYCStatus.NOT_STARTED, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # TODO: relationship to KYCApplication, Beneficiary, Wallet, Remittance
