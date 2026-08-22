"""
Mock KYC application. Reviewed/approved or rejected by an admin.

ApplicationStatus (this file) is deliberately a separate enum from User.KYCStatus
(app.models.user) rather than a reuse: KYCStatus has a NOT_STARTED member that is
meaningless for an application row (an application only ever exists once submitted).
routers/admin.py's approve/reject endpoints are what keep the two in sync:
  ApplicationStatus.APPROVED -> User.kyc_status = KYCStatus.APPROVED
  ApplicationStatus.REJECTED -> User.kyc_status = KYCStatus.REJECTED
  ApplicationStatus.PENDING  -> User.kyc_status = KYCStatus.PENDING
"""
import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import relationship

from app.database import Base


class ApplicationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KYCApplication(Base):
    __tablename__ = "kyc_applications"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False)

    full_name = Column(String, nullable=False)
    date_of_birth = Column(Date, nullable=False)
    nationality = Column(String, nullable=False)
    identification_number = Column(String, nullable=False)
    residential_address = Column(String, nullable=False)
    mobile_number = Column(String, nullable=False)
    email = Column(String, nullable=False)
    source_of_funds = Column(String, nullable=False)

    status = Column(Enum(ApplicationStatus), default=ApplicationStatus.PENDING, nullable=False)
    reviewed_by_admin_id = Column(Uuid, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)  # set only when status == REJECTED
    submitted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="kyc_applications", foreign_keys=[user_id])
