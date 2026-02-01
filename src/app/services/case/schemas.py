import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FinancialSummaryResponse(BaseModel):
    total_revenue: Decimal
    pending_payments: int
    pending_amount: Decimal
    average_case_cost: Decimal
    total_cases: int
    completed_cases: int
    active_cases: int
    overdue_cases: int


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


# === Nested Schemas ===


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
    direction: str  # e.g., "incoming" or "outgoing"
    created_at: datetime


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
    assigned_user_id: uuid.UUID | None = None
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
    assigned_user_id: uuid.UUID | None = None  # renamed from assigned_expert_id for consistency
    archive_status: str | None = None
    remarks: str | None = None


class CaseResponse(CaseBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    assigned_expert: UserResponse | None = None


class SortField(str, Enum):
    # Поля для сортировки
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
    CLIENT_NAME = "client_name"  # Сортировка по имени клиента (через join)
    EXPERT_NAME = "expert_name"  # Сортировка по имени эксперта (через join)


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class GetCasesQuery(BaseModel):
    # Фильтры
    status: list[CaseStatus] | None = None
    expert_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None

    # Новые фильтры
    case_type: str | None = None  # Тип дела (административное, гражданское и т.д.)
    object_type: str | None = None  # Тип объекта (земля, недвижимость и т.д.)
    authority: str | None = None  # Орган власти/суд
    object_address: str | None = None  # Адрес объекта (поиск по частичному совпадению)
    number: str | None = None  # Номер дела (точное совпадение)
    case_number: str | None = None  # Номер производства (точное совпадение)
    search: str | None = None  # Поиск по нескольким полям (общий поиск)

    # Фильтры по стоимости
    min_cost: Decimal | None = None
    max_cost: Decimal | None = None
    min_remaining_debt: Decimal | None = None
    max_remaining_debt: Decimal | None = None

    # Фильтры по датам (дополнительные)
    completion_start_date: datetime | None = None  # Начальная дата завершения
    completion_end_date: datetime | None = None  # Конечная дата завершения
    deadline_start_date: datetime | None = None  # Начальная дата крайнего срока
    deadline_end_date: datetime | None = None  # Конечная дата крайнего срока

    # Сортировка
    sort_field: SortField | None = SortField.CREATED_AT
    sort_order: SortOrder | None = SortOrder.DESC

    # Пагинация
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
    model_config = ConfigDict(from_attributes=True)

    case: CaseResponse
    client: ClientResponse
    assigned_experts: list[UserResponse] = Field(default_factory=list)
    documents: list[DocumentResponse] = Field(default_factory=list)
    events: list[MailMessageResponse] = Field(default_factory=list)


class CaseSuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    case_number: str
