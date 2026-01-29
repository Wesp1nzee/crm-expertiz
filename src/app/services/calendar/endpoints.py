from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.database.session import get_db
from src.app.services.calendar.models import CalendarActivity, CalendarEvent, CalendarTask
from src.app.services.calendar.schemas import CalendarResponse, EventCreate, EventUpdate, TaskCreate
from src.app.services.calendar.service import CalendarService
from src.app.services.user.models import User

router = APIRouter(prefix="/api/calendar", tags=["Calendar"])


@router.get("/", response_model=Sequence[CalendarResponse])
async def get_calendar_activities(
    start: datetime = Query(..., description="Начало периода (ISO)"),
    end: datetime = Query(..., description="Конец периода (ISO)"),
    only_mine: bool = Query(False, description="Показать только мои события"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Sequence[CalendarActivity]:
    service = CalendarService(db)
    user_filter = None
    if current_user.role == "admin" or current_user.role == "ceo":
        user_filter = current_user.id if only_mine else None
    return await service.get_activities(current_user.company_id, start, end, user_filter)


@router.post("/event", response_model=CalendarResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    schema: EventCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CalendarEvent:
    service = CalendarService(db)
    event = await service.create_event(current_user.company_id, current_user.id, schema)

    background_tasks.add_task(service.schedule_reminder, event.id)
    return event


@router.post("/task", response_model=CalendarResponse, status_code=status.HTTP_201_CREATED)
async def create_task(schema: TaskCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> CalendarTask:
    service = CalendarService(db)
    return await service.create_task(current_user.company_id, current_user.id, schema)


@router.patch("/{activity_id}", response_model=CalendarResponse)
async def update_activity(
    activity_id: UUID, schema: EventUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CalendarActivity:
    service = CalendarService(db)
    activity = await service.update_activity(activity_id, current_user.company_id, schema)
    if not activity:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return activity


@router.post("/{activity_id}/toggle", response_model=CalendarResponse)
async def toggle_task_status(
    activity_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CalendarTask:
    service = CalendarService(db)
    activity = await service.toggle_task_status(activity_id, current_user.company_id)
    if not activity:
        raise HTTPException(status_code=404, detail="Задача не найдена или это не задача")
    return activity


@router.delete("/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity(activity_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    service = CalendarService(db)
    success = await service.delete_activity(activity_id, current_user.company_id)
    if not success:
        raise HTTPException(status_code=404, detail="Запись не найдена")
