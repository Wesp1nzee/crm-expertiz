import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DB_URL: str
    DEBUG: bool = False

    S3_ENDPOINT_URL: str
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_BUCKET_NAME: str
    S3_REGION: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    REDIS_URL: str = "redis://localhost"

    ADMIN_EMAIL: str
    ADMIN_FULL_NAME: str
    ADMIN_PASSWORD: str

    MAIL_EMAIL: str
    MAIL_PASSWORD: str
    MAIL_IMAP_HOST: str = "imap.yandex.ru"
    MAIL_IMAP_PORT: int = 993
    MAIL_SMTP_HOST: str = "smtp.yandex.ru"
    MAIL_SMTP_PORT: int = 465
    MAIL_COMPANY_ID: uuid.UUID

    DADATA_API_KEY: str


settings = Settings()
