from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.services.calendar.models import ActivityType, EventStatus


class CalendarBase(BaseModel):
    title: str = Field(..., max_length=255)
    description: str | None = None
    color: str | None = Field("#3788d8", pattern="^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$")
    all_day: bool = False
    start_at: datetime

    case_id: UUID | None = None
    client_id: UUID | None = None


class EventCreate(CalendarBase):
    type: ActivityType = ActivityType.EVENT
    end_at: datetime
    location: str | None = None
    attendee_ids: list[UUID] = []

    @model_validator(mode="after")
    def validate_dates(self) -> EventCreate:
        if self.end_at <= self.start_at:
            raise ValueError("Дата окончания должна быть позже даты начала")
        return self


class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    location: str | None = None
    status: EventStatus | None = None
    attendee_ids: list[UUID] | None = None
    color: str | None = None


class TaskCreate(CalendarBase):
    type: ActivityType = ActivityType.TASK
    end_at: datetime | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    start_at: datetime | None = None
    is_completed: bool | None = None
    color: str | None = None


class UserShort(BaseModel):
    id: UUID
    full_name: str
    model_config = ConfigDict(from_attributes=True)


class CalendarResponse(CalendarBase):
    id: UUID
    type: ActivityType
    creator_id: UUID
    end_at: datetime | None = None
    location: str | None = None
    status: EventStatus | None = None
    is_completed: bool
    completed_at: datetime | None = None

    attendees: list[UserShort] = []

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
