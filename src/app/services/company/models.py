import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Boolean, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base

if TYPE_CHECKING:
    from src.app.services.client.models import Client
    from src.app.services.user.models import User


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    inn: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)

    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    balance: Mapped[Decimal] = mapped_column(Numeric(precision=15, scale=2), default=Decimal("0.00"), server_default="0.00")
    currency: Mapped[str] = mapped_column(String(3), default="RUB", server_default="RUB")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_trial: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # СВЯЗИ
    users: Mapped[list[User]] = relationship("User", back_populates="company", cascade="all, delete-orphan")
    # Добавляем связь с клиентами
    clients: Mapped[list[Client]] = relationship("Client", back_populates="company", cascade="all, delete-orphan")
