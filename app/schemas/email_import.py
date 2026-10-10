from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class GmailConfirmation(BaseModel):
  code: Optional[str] = None
  link: Optional[str] = None
  received_at: datetime


class EmailImportItem(BaseModel):
  id: str
  status: str
  filename: str
  sender: Optional[str] = None
  subject: Optional[str] = None
  error_message: Optional[str] = None
  created_at: datetime
  statement_id: Optional[str] = None
  statement_status: Optional[str] = None
  bank_name: Optional[str] = None
  period_start: Optional[date] = None
  period_end: Optional[date] = None


class EmailImportOverview(BaseModel):
  enabled: bool
  address: Optional[str] = None
  gmail_confirmation: Optional[GmailConfirmation] = None
  imports: List[EmailImportItem]


class BankPasswordIn(BaseModel):
  bank_name: str = Field(min_length=1, max_length=100)
  password: str = Field(min_length=1, max_length=200)


class BankPasswordItem(BaseModel):
  """The password itself is never returned."""
  id: str
  bank_name: str
  updated_at: datetime
