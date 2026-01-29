from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.services.calendar.models import ActivityType, CalendarActivity, CalendarEvent, CalendarTask, event_attendees
from src.app.services.calendar.schemas import EventCreate, EventUpdate, TaskCreate, TaskUpdate
from src.app.services.user.models import User


class CalendarService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_activities(
        self, company_id: UUID, start_range: datetime, end_range: datetime, user_id: UUID | None = None
    ) -> Sequence[CalendarActivity]:
        query = (
            select(CalendarActivity)
            .where(
                and_(CalendarActivity.company_id == company_id, CalendarActivity.start_at >= start_range, CalendarActivity.start_at <= end_range)
            )
            .options(selectinload(CalendarActivity.attendees))
        )

        if user_id:
            query = query.join(event_attendees, isouter=True).where(
                or_(CalendarActivity.creator_id == user_id, event_attendees.c.user_id == user_id)
            )

        result = await self.db.execute(query)
        return result.scalars().all()

    async def create_event(self, company_id: UUID, creator_id: UUID, schema: EventCreate) -> CalendarEvent:
        new_event = CalendarEvent(**schema.model_dump(exclude={"attendee_ids"}), company_id=company_id, creator_id=creator_id)

        if schema.attendee_ids:
            result = await self.db.execute(select(User).where(User.id.in_(schema.attendee_ids)))
            new_event.attendees = list(result.scalars().all())

        self.db.add(new_event)
        await self.db.commit()
        new_event_id = new_event.id

        refreshed_result = await self.db.execute(
            select(CalendarEvent).where(CalendarEvent.id == new_event_id).options(selectinload(CalendarEvent.attendees))
        )
        new_event = refreshed_result.scalar_one()
        return new_event

    async def create_task(self, company_id: UUID, creator_id: UUID, schema: TaskCreate) -> CalendarTask:
        new_task = CalendarTask(**schema.model_dump(), company_id=company_id, creator_id=creator_id, is_completed=False)
        self.db.add(new_task)
        await self.db.commit()
        new_task_id = new_task.id
        refreshed_result = await self.db.execute(
            select(CalendarTask).where(CalendarTask.id == new_task_id).options(selectinload(CalendarTask.attendees))
        )
        new_task = refreshed_result.scalar_one()
        return new_task

    async def update_activity(self, activity_id: UUID, company_id: UUID, schema: EventUpdate | TaskUpdate) -> CalendarActivity | None:
        query = (
            select(CalendarActivity)
            .where(and_(CalendarActivity.id == activity_id, CalendarActivity.company_id == company_id))
            .options(selectinload(CalendarActivity.attendees))
        )

        result = await self.db.execute(query)
        activity = result.scalar_one_or_none()

        if not activity:
            return None

        data = schema.model_dump(exclude_unset=True)

        if "attendee_ids" in data and activity.type == ActivityType.EVENT:
            ids = data.pop("attendee_ids")
            users_res = await self.db.execute(select(User).where(User.id.in_(ids)))
            activity.attendees = list(users_res.scalars().all())

        if "is_completed" in data:
            activity.completed_at = datetime.now() if data["is_completed"] else None

        for key, value in data.items():
            setattr(activity, key, value)

        await self.db.commit()
        await self.db.refresh(activity)
        return activity

    async def delete_activity(self, activity_id: UUID, company_id: UUID) -> bool:
        query = delete(CalendarActivity).where(and_(CalendarActivity.id == activity_id, CalendarActivity.company_id == company_id))
        result = await self.db.execute(query)
        await self.db.commit()
        return bool(cast(CursorResult[Any], result).rowcount > 0)

    async def toggle_task_status(self, task_id: UUID, company_id: UUID) -> CalendarTask | None:
        activity = await self.db.get(CalendarActivity, task_id)

        if not activity or activity.type != ActivityType.TASK or activity.company_id != company_id:
            return None

        if not isinstance(activity, CalendarTask):
            return None

        activity.is_completed = not activity.is_completed
        activity.completed_at = datetime.now() if activity.is_completed else None

        await self.db.commit()
        await self.db.refresh(activity)
        return activity

    async def schedule_reminder(self, activity_id: UUID) -> None:
        # TODO:
        # Здесь логика уведомлений
        pass
