import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.app.core.schemas.base import PaginatedResponse, PaginationMeta


class EfficiencyMetrics(BaseModel):
    avg_completion_time: float
    conversion_rate: float
    conversion_trend: float
    throughput: float


class RecentCaseItem(BaseModel):
    id: uuid.UUID
    number: str
    case_number: str
    status: CaseStatus
    cost: Decimal
    created_at: datetime
    client_id: uuid.UUID


class FinancialSummaryResponse(BaseModel):
    total_revenue: Decimal
    pending_payments: int
    pending_amount: Decimal
    average_case_cost: Decimal
    total_cases: int
    completed_cases: int
    active_cases: int
    overdue_cases: int
    efficiency: EfficiencyMetrics
    recent_cases: list[RecentCaseItem]


class CaseStatus(str, Enum):
    archive = "archive"
    in_work = "in_work"
    debt = "debt"
    executed = "executed"
    withdrawn = "withdrawn"
    cancelled = "cancelled"
    fssp = "fssp"


class ClientType(str, Enum):
    legal = "legal"
    individual = "individual"
    court = "court"


class ContactType(str, Enum):
    legal_representative = "legal_representative"
    court_officer = "court_officer"
    individual = "individual"


# ── Nested Schemas ────────────────────────────────────────────────────────────


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    position: str | None = None
    email: str | None = None
    phone: str | None = None
    is_main: bool
    contact_type: ContactType


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    short_name: str | None = None
    type: ClientType
    inn: str | None = None
    email: str | None = None
    phone: str | None = None
    legal_address: str | None = None
    actual_address: str | None = None
    contacts: list[ContactResponse] = Field(default_factory=list)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None


class FolderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    original_filename: str
    file_path: str
    file_size: int
    mime_type: str
    file_extension: str
    version: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    folder: FolderResponse | None = None
    uploaded_by: UserResponse | None = None


class MailMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject: str
    body: str | None = None
    sent_at: datetime
    direction: str
    created_at: datetime


class MailMessageDetailResponse(BaseModel):
    """Detailed mail response for case details view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    external_message_id: str | None
    thread_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    user_id: uuid.UUID
    case_id: uuid.UUID | None
    sender_email: str
    sender_name: str | None
    reply_to: str | None
    subject: str | None
    folder: str
    message_type: str
    status: str
    is_read: bool
    is_important: bool
    is_starred: bool
    is_spam: bool
    is_archived: bool
    is_deleted: bool
    size_bytes: int | None
    sent_at: datetime | None
    processed_at: datetime
    updated_at: datetime
    body_text: str | None = None
    body_html: str | None = None
    attachment_count: int = 0


# ── Case Schemas ──────────────────────────────────────────────────────────────


class CaseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    client_id: uuid.UUID
    number: str = Field(..., max_length=50)
    case_number: str = Field(..., max_length=100)
    authority: str
    case_type: str
    object_type: str
    object_address: str
    status: CaseStatus = CaseStatus.in_work
    start_date: datetime
    deadline: datetime
    completion_date: datetime | None = None
    cost: Decimal
    bank_transfer_amount: Decimal = Decimal("0.00")
    cash_amount: Decimal = Decimal("0.00")
    remaining_debt: Decimal = Decimal("0.00")
    plaintiff: str | None = None
    defendant: str | None = None
    expert_painting: str | None = None
    archive_status: str | None = None
    remarks: str | None = None
    judge_name: str | None = None


class CaseCreateRequest(CaseBase):
    # Список экспертов при создании (опционально)
    expert_ids: list[uuid.UUID] = Field(default_factory=list)


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
    archive_status: str | None = None
    remarks: str | None = None
    judge_name: str | None = None

    @field_validator("start_date", "deadline", "completion_date", mode="after")
    @classmethod
    def make_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class AssignExpertsRequest(BaseModel):
    """Тело запроса для назначения/замены списка экспертов на дело."""

    expert_ids: list[uuid.UUID]


class CaseResponse(CaseBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Список экспертов вместо одного assigned_expert
    experts: list[UserResponse] = Field(default_factory=list)


class SortField(str, Enum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    START_DATE = "start_date"
    DEADLINE = "deadline"
    COMPLETION_DATE = "completion_date"
    NUMBER = "number"
    CASE_NUMBER = "case_number"
    STATUS = "status"
    COST = "cost"
    REMAINING_DEBT = "remaining_debt"
    CLIENT_NAME = "client_name"
    EXPERT_NAME = "expert_name"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class GetCasesQuery(BaseModel):
    status: list[CaseStatus] | None = None
    # Фильтр по эксперту — ищет дела где этот пользователь в списке экспертов
    expert_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    case_type: str | None = None
    object_type: str | None = None
    authority: str | None = None
    object_address: str | None = None
    number: str | None = None
    case_number: str | None = None
    search: str | None = None
    min_cost: Decimal | None = None
    max_cost: Decimal | None = None
    min_remaining_debt: Decimal | None = None
    max_remaining_debt: Decimal | None = None
    completion_start_date: datetime | None = None
    completion_end_date: datetime | None = None
    deadline_start_date: datetime | None = None
    deadline_end_date: datetime | None = None
    sort_field: SortField | None = SortField.CREATED_AT
    sort_order: SortOrder | None = SortOrder.DESC
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class CasesPaginationMeta(PaginationMeta):
    active: int
    overdue: int
    completed: int


class GetCasesResponse(PaginatedResponse[CaseResponse]):
    meta: CasesPaginationMeta


class CaseDetailsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case: CaseResponse
    client: ClientResponse
    experts: list[UserResponse] = Field(default_factory=list)
    documents: list[DocumentResponse] = Field(default_factory=list)
    messages: list[MailMessageDetailResponse] = Field(default_factory=list)
    folders: list[FolderResponse] = Field(default_factory=list)


class CaseSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    case_number: str
