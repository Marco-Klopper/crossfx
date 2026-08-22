"""
Mock KYC application. Reviewed/approved or rejected by an admin.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import Column, Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class KYCApplication(Base):
    __tablename__ = "kyc_applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    full_name = Column(String, nullable=False)
    date_of_birth = Column(Date, nullable=False)
    nationality = Column(String, nullable=False)
    identification_number = Column(String, nullable=False)
    residential_address = Column(String, nullable=False)
    mobile_number = Column(String, nullable=False)
    email = Column(String, nullable=False)
    source_of_funds = Column(String, nullable=False)

    status = Column(String, default="pending", nullable=False)  # pending | approved | rejected
    reviewed_by_admin_id = Column(UUID(as_uuid=True), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)
