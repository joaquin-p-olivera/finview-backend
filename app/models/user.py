import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    # NULL for accounts created with Google, which only sign in through it
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Google's id for the account ("sub" of its ID token), set on the first Google sign-in
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # secret part of the user's email import address (finview.import+TOKEN@gmail.com)
    import_token: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    # email the user when the import inbox handles one of their statements
    email_notifications: Mapped[bool] = mapped_column(
        Boolean, server_default=true(), default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    @property
    def google_linked(self) -> bool:
        return bool(self.google_id)

    categories: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    statements: Mapped[list["Statement"]] = relationship(
        "Statement",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="user",
        cascade="all, delete-orphan",
    )

