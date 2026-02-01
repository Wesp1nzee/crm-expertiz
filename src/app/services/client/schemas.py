from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ClientType(str, Enum):
    legal = "legal"
    individual = "individual"
    court = "court"


class ContactType(str, Enum):
    legal_representative = "legal_representative"
    court_officer = "court_officer"
    individual = "individual"


class ContactBase(BaseModel):
    name: str = Field(..., max_length=255, examples=["Иванов Иван Иванович"])
    position: str | None = Field(None, max_length=255, examples=["Главный юрист"])
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50, examples=["+7 (999) 000-00-00"])
    is_main: bool = False
    contact_type: ContactType = ContactType.individual


class ContactCreate(ContactBase):
    """Схема для создания контакта напрямую (через /contacts)"""

    client_id: UUID


class ContactUpdate(BaseModel):
    """Схема для частичного обновления контакта"""

    name: str | None = None
    position: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    is_main: bool | None = None
    contact_type: ContactType | None = None


class ContactResponse(ContactBase):
    """Схема ответа контакта"""

    id: UUID
    client_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClientBase(BaseModel):
    name: str = Field(..., max_length=255, examples=['ООО "Эксперт-Групп"'])
    short_name: str | None = Field(None, max_length=100)
    type: ClientType
    inn: str | None = Field(None, min_length=10, max_length=12, examples=["7700000000"])
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    legal_address: str | None = None
    actual_address: str | None = None


class ClientCreate(ClientBase):
    """Схема создания клиента"""

    initial_contact: ContactBase | None = None


class ClientUpdate(BaseModel):
    """Схема частичного обновления клиента"""

    name: str | None = None
    short_name: str | None = None
    type: ClientType | None = None
    inn: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    legal_address: str | None = None
    actual_address: str | None = None


class ClientShortResponse(ClientBase):
    """Для списков (без тяжелых связей)"""

    id: UUID
    created_at: datetime
    active_cases: int = 0  # Количество активных дел
    total_cases: int = 0  # Общее количество дел

    model_config = ConfigDict(from_attributes=True)


class ClientFullResponse(ClientShortResponse):
    """Полная инфа о клиенте со всеми контактами"""

    updated_at: datetime
    contacts: list[ContactResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ClientFilters(BaseModel):
    """Для GET /clients запросов"""

    type: ClientType | None = None
    search: str | None = Field(None, description="Поиск по имени или ИНН")
    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class ClientListResponse(BaseModel):
    """Ответ для списка клиентов с метаданными"""

    items: list[ClientShortResponse]
    total: int
    page: int
    size: int
    pages: int


class SearchResultDTO(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)
