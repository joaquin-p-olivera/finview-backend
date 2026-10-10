from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


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
