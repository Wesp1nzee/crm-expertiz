from pydantic import BaseModel, Field


class InnLookupResult(BaseModel):
    """Результат поиска организации по ИНН через Dadata."""

    inn: str
    kpp: str | None = None
    ogrn: str | None = None
    full_name: str
    short_name: str
    legal_form: str | None = None
    is_individual: bool
    status: str
    status_code: int | None = Field(None, description="Детальный статус организации по классификатору Dadata")
    status_description: str | None = Field(None, description="Описание детального статуса организации")
    is_warning: bool = False
    warning_message: str | None = None
    registration_date: int | None = None
    liquidation_date: int | None = None
    address: str | None = None
    city: str | None = None
    ceo_name: str | None = None
    ceo_post: str | None = None
    okved: str | None = None
    phones: list[str] = Field(default_factory=list, description="Список телефонов организации")
    emails: list[str] = Field(default_factory=list, description="Список email адресов организации")
