import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Beneficiary(Base):
    __tablename__ = "beneficiaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    full_name = Column(String, nullable=False)
    contact = Column(String, nullable=False)  # mobile number or email
    country = Column(String, nullable=False)
    preferred_payout_currency = Column(String, nullable=False)  # e.g. USD, RLUSD
    relationship_to_sender = Column(String, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
