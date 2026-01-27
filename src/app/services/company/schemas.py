import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CompanyBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Название организации")
    inn: str = Field(..., min_length=10, max_length=12, pattern=r"^\d+$", description="ИНН")
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None


class CompanyRegister(CompanyBase):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str


class CompanyUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None


class CompanyResponse(CompanyBase):
    id: uuid.UUID
    balance: Decimal
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
