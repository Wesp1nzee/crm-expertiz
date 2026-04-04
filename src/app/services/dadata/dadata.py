from pydantic import BaseModel, Field


class AddressSuggestion(BaseModel):
    """Предложение адреса от Dadata."""

    value: str = Field(description="Адрес одной строкой")
    unrestricted_value: str = Field(description="Адрес одной строкой (полный)")


class AddressLookupResult(BaseModel):
    """Результат поиска адресов через Dadata."""

    suggestions: list[AddressSuggestion] = Field(default_factory=list, description="Список предложенных адресов")


class CourtSuggestion(BaseModel):
    """Предложение суда от Dadata."""

    value: str = Field(description="Значение одной строкой")
    unrestricted_value: str = Field(description="= value")
    code: str = Field(description="Код суда (уникальный)")
    name: str = Field(description="Полное название суда")
    inn: str | None = Field(default=None, description="ИНН суда")
    court_type: str = Field(description="Тип суда")
    court_type_name: str = Field(description="Наименование типа суда")
    address: str | None = Field(default=None, description="Адрес суда")
    legal_address: str | None = Field(default=None, description="Юридический адрес суда")
    website: str | None = Field(default=None, description="Сайт суда")


class CourtLookupResult(BaseModel):
    """Результат поиска судов через Dadata."""

    suggestions: list[CourtSuggestion] = Field(default_factory=list, description="Список предложенных судов")


class PartySuggestion(BaseModel):
    """Предложение организации от Dadata."""

    value: str = Field(description="Наименование компании")
    unrestricted_value: str = Field(description="= value")
    inn: str | None = Field(default=None, description="ИНН")
    kpp: str | None = Field(default=None, description="КПП")
    kpp_largest: str | None = Field(default=None, description="КПП крупнейшего налогоплательщика")
    ogrn: str | None = Field(default=None, description="ОГРН")
    ogrn_date: int | None = Field(default=None, description="Дата выдачи ОГРН (timestamp)")
    hid: str | None = Field(default=None, description="Внутренний идентификатор в Дадате")
    type: str | None = Field(default=None, description="Тип организации (LEGAL/INDIVIDUAL)")
    name_full_with_opf: str | None = Field(default=None, description="Полное наименование с ОПФ")
    name_short_with_opf: str | None = Field(default=None, description="Краткое наименование с ОПФ")
    name_full: str | None = Field(default=None, description="Полное наименование без ОПФ")
    name_short: str | None = Field(default=None, description="Краткое наименование без ОПФ")
    fio_surname: str | None = Field(default=None, description="Фамилия ИП")
    fio_name: str | None = Field(default=None, description="Имя ИП")
    fio_patronymic: str | None = Field(default=None, description="Отчество ИП")
    okato: str | None = Field(default=None, description="Код ОКАТО")
    oktmo: str | None = Field(default=None, description="Код ОКТМО")
    okpo: str | None = Field(default=None, description="Код ОКПО")
    okogu: str | None = Field(default=None, description="Код ОКОГУ")
    okfs: str | None = Field(default=None, description="Код ОКФС")
    okved: str | None = Field(default=None, description="Код ОКВЭД")
    okved_type: str | None = Field(default=None, description="Версия справочника ОКВЭД")
    opf_code: str | None = Field(default=None, description="Код ОКОПФ")
    opf_full: str | None = Field(default=None, description="Полное название ОПФ")
    opf_short: str | None = Field(default=None, description="Краткое название ОПФ")
    opf_type: str | None = Field(default=None, description="Версия справочника ОКОПФ")
    management_name: str | None = Field(default=None, description="Наименование/ФИО руководителя")
    management_post: str | None = Field(default=None, description="Должность руководителя")
    management_start_date: int | None = Field(default=None, description="Дата вступления в должность (timestamp)")
    branch_count: int | None = Field(default=None, description="Количество филиалов")
    branch_type: str | None = Field(default=None, description="Тип подразделения (MAIN/BRANCH)")
    address_value: str | None = Field(default=None, description="Адрес одной строкой")
    address_unrestricted_value: str | None = Field(default=None, description="Адрес одной строкой (полный)")
    address_source: str | None = Field(default=None, description="Адрес одной строкой как в ЕГРЮЛ")
    address_qc: int | None = Field(default=None, description="Код проверки адреса")
    state_actualty_date: int | None = Field(default=None, description="Дата последних изменений (timestamp)")
    state_registration_date: int | None = Field(default=None, description="Дата регистрации (timestamp)")
    state_liquidation_date: int | None = Field(default=None, description="Дата ликвидации (timestamp)")
    state_status: str | None = Field(default=None, description="Статус организации")
    state_code: int | None = Field(default=None, description="Детальный статус")
    state_status_description: str | None = Field(default=None, description="Описание статуса")


class PartyLookupResult(BaseModel):
    """Результат поиска организаций через Dadata."""

    suggestions: list[PartySuggestion] = Field(default_factory=list, description="Список предложенных организаций")


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
