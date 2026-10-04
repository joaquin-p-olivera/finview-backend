from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel


class StatementBase(BaseModel):
  id: str
  filename: Optional[str] = None
  bank_name: Optional[str] = None
  period_start: Optional[date] = None
  period_end: Optional[date] = None
  card_last4: Optional[str] = None
  currency: str = "UYU"
  status: Optional[str] = None
  uploaded_at: datetime
  confirmed_at: Optional[datetime] = None

  model_config = {"from_attributes": True}


class StatementListItem(StatementBase):
  pass


class TransactionForReview(BaseModel):
  id: str
  date: date
  description: str
  merchant: Optional[str] = None
  amount: float
  currency: str
  installment_num: Optional[int] = None
  installment_tot: Optional[int] = None
  suggested_category: Optional[str] = None
  category_id: Optional[str] = None
  category_source: Literal["ai", "user"] = "ai"


class StatementDetail(StatementBase):
  transactions: List[TransactionForReview]


class StatementStatus(BaseModel):
  id: str
  status: Optional[str] = None
  error_message: Optional[str] = None


class TransactionConfirm(BaseModel):
  date: date
  description: str
  merchant: Optional[str] = None
  amount: float
  currency: str
  installment_num: Optional[int] = None
  installment_tot: Optional[int] = None
  category_id: Optional[str] = None
  category_source: Literal["ai", "user"] = "user"


class StatementConfirmRequest(BaseModel):
  transactions: List[TransactionConfirm]


class ExternalTransaction(BaseModel):
  date: date
  description: str
  merchant: Optional[str] = None
  amount: float
  currency: str
  installment_num: Optional[int] = None
  installment_tot: Optional[int] = None
  category_name: Optional[str] = None


class ExternalStatementSummary(BaseModel):
  """Official totals printed by the bank (they include insurance, interest and fees,
  which are not sent as transactions)."""
  previous_balance_uyu: Optional[float] = None
  previous_balance_usd: Optional[float] = None
  payments_uyu: Optional[float] = None
  payments_usd: Optional[float] = None
  insurance_uyu: Optional[float] = None
  insurance_usd: Optional[float] = None
  interest_uyu: Optional[float] = None
  interest_usd: Optional[float] = None
  fees_uyu: Optional[float] = None
  fees_usd: Optional[float] = None
  statement_total_uyu: Optional[float] = None
  statement_total_usd: Optional[float] = None


class ExternalStatementRequest(BaseModel):
  bank_name: str
  card_last4: Optional[str] = None
  period_start: date
  period_end: date
  summary: Optional[ExternalStatementSummary] = None
  transactions: List[ExternalTransaction]
