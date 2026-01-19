import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaseStatus(str, Enum):
    archive = "archive"
    in_work = "in_work"
    debt = "debt"
    executed = "executed"
    withdrawn = "withdrawn"
    cancelled = "cancelled"
    fssp = "fssp"


class CaseBase(BaseModel):
    number: str = Field(..., max_length=50)
    case_number: str = Field(..., max_length=100)
    authority: str = Field(..., max_length=255)
    client_id: uuid.UUID
    case_type: str = Field(..., max_length=100)
    object_type: str = Field(..., max_length=100)
    object_address: str
    status: CaseStatus = CaseStatus.in_work
    start_date: datetime
    deadline: datetime
    cost: Decimal
    plaintiff: str | None = None
    defendant: str | None = None
    bank_transfer_amount: Decimal = Decimal("0.00")
    cash_amount: Decimal = Decimal("0.00")
    remaining_debt: Decimal = Decimal("0.00")
    completion_date: datetime | None = None
    assigned_expert_id: str | None = None
    archive_status: str | None = None
    remarks: str | None = None


class CaseCreateRequest(CaseBase):
    pass


class CaseUpdateRequest(BaseModel):
    number: str | None = None
    case_number: str | None = None
    authority: str | None = None
    client_id: uuid.UUID | None = None
    case_type: str | None = None
    object_type: str | None = None
    object_address: str | None = None
    status: CaseStatus | None = None
    start_date: datetime | None = None
    deadline: datetime | None = None
    cost: Decimal | None = None
    plaintiff: str | None = None
    defendant: str | None = None
    bank_transfer_amount: Decimal | None = None
    cash_amount: Decimal | None = None
    remaining_debt: Decimal | None = None
    completion_date: datetime | None = None
    assigned_expert_id: str | None = None
    archive_status: str | None = None
    remarks: str | None = None


class CaseResponse(CaseBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GetCasesQuery(BaseModel):
    status: list[CaseStatus] | None = None
    expert_id: str | None = None
    client_id: uuid.UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class PaginationInfo(BaseModel):
    total: int
    page: int
    limit: int
    total_pages: int


class CasesSummary(BaseModel):
    active: int
    overdue: int
    completed: int


class GetCasesResponse(BaseModel):
    data: list[CaseResponse]
    pagination: PaginationInfo
    summary: CasesSummary


class CaseDetailsResponse(BaseModel):
    case: CaseResponse
    assigned_experts: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
