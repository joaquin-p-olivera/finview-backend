import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class BankPdfPassword(Base):
    """Password of a user's password-protected statement PDFs for one bank (e.g.
    Santander uses the holder's ID number). Stored encrypted; the email import
    tries it when a protected PDF arrives."""

    __tablename__ = "bank_pdf_passwords"
    __table_args__ = (UniqueConstraint("user_id", "bank_key", name="uq_bank_pdf_password_user_bank"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # lowercase bank_name, to keep one password per bank ignoring case
    bank_key: Mapped[str] = mapped_column(String(100), nullable=False)
    # Fernet token (see services/secret_box.py)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
