import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class EmailImport(Base):
    """One thing found in the import inbox for a user: a statement PDF (one row
    per attachment) or Gmail's forwarding confirmation. The email itself is
    deleted once handled; this row is what the user sees."""

    __tablename__ = "email_imports"
    __table_args__ = (UniqueConstraint("user_id", "message_id", "filename", name="uq_email_import_attachment"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # "statement" or "gmail_confirmation"
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # processing | done | error | duplicate
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    statement_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("statements.id", ondelete="SET NULL"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # gmail_confirmation: {"code": ..., "link": ...}
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
