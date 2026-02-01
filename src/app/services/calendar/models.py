import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base

if TYPE_CHECKING:
    from src.app.services.case.models import Case
    from src.app.services.client.models import Client
    from src.app.services.user.models import User


class ActivityType(str, Enum):
    EVENT = "event"
    TASK = "task"


class EventStatus(str, Enum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


event_attendees = Table(
    "calendar_event_attendees",
    Base.metadata,
    Column("activity_id", ForeignKey("calendar_activities.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class CalendarActivity(Base):
    __tablename__ = "calendar_activities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    type: Mapped[ActivityType] = mapped_column(SQLEnum(ActivityType, native_enum=False), nullable=False, index=True)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(20))

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)

    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    location: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EventStatus] = mapped_column(SQLEnum(EventStatus, native_enum=False), default=EventStatus.SCHEDULED)

    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"), nullable=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    creator: Mapped[User] = relationship("User", foreign_keys=[creator_id])
    attendees: Mapped[list[User]] = relationship("User", secondary=event_attendees)
    case: Mapped[Case | None] = relationship("Case")
    client: Mapped[Client | None] = relationship("Client")

    __mapper_args__ = {
        "polymorphic_on": type,
        "polymorphic_identity": "activity",
    }


class CalendarEvent(CalendarActivity):
    """Мероприятие: встреча, звонок, заседание"""

    __mapper_args__ = {"polymorphic_identity": ActivityType.EVENT}


class CalendarTask(CalendarActivity):
    """Задача: напомнить, сделать отчет, проверить почту"""

    __mapper_args__ = {"polymorphic_identity": ActivityType.TASK}
