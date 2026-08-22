"""
A recipient a sender has registered to remit to. One sender can have many beneficiaries;
a beneficiary belongs to exactly one sender (no shared/pooled beneficiaries).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import relationship

from app.database import Base


class Beneficiary(Base):
    __tablename__ = "beneficiaries"
    __table_args__ = (
        # Stops a sender registering the same recipient twice — a real edge case
        # in a demo where "contact" is typed by hand more than once.
        UniqueConstraint("sender_id", "contact", name="uq_beneficiary_sender_contact"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    sender_id = Column(Uuid, ForeignKey("users.id"), nullable=False)

    full_name = Column(String, nullable=False)
    contact = Column(String, nullable=False)  # mobile number or email
    country = Column(String, nullable=False)
    preferred_payout_currency = Column(String, nullable=False)  # e.g. USD, RLUSD
    relationship_to_sender = Column(String, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sender = relationship("User", back_populates="beneficiaries")
