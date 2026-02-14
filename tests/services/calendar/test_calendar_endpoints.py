from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from src.app.services.calendar import endpoints as calendar_endpoints
from src.app.services.user.models import UserRole


@pytest.mark.asyncio
async def test_calendar_endpoints(client: AsyncClient, current_user: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    activity_id = uuid4()

    async def fake_get_activities(self, company_id, start, end, user_filter):  # type: ignore[no-untyped-def]
        return []

    async def fake_create_event(self, company_id, user_id, schema):  # type: ignore[no-untyped-def]
        now = datetime.utcnow()
        return {
            "id": str(activity_id),
            "title": schema.title,
            "description": schema.description,
            "color": schema.color,
            "all_day": schema.all_day,
            "start_at": schema.start_at.isoformat(),
            "case_id": None,
            "client_id": None,
            "type": "event",
            "creator_id": str(user_id),
            "end_at": schema.end_at.isoformat(),
            "location": schema.location,
            "status": "scheduled",
            "is_completed": False,
            "completed_at": None,
            "attendees": [],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    async def fake_create_task(self, company_id, user_id, schema):  # type: ignore[no-untyped-def]
        now = datetime.utcnow()
        return {
            "id": str(activity_id),
            "title": schema.title,
            "description": schema.description,
            "color": schema.color,
            "all_day": schema.all_day,
            "start_at": schema.start_at.isoformat(),
            "case_id": None,
            "client_id": None,
            "type": "task",
            "creator_id": str(user_id),
            "end_at": None,
            "location": None,
            "status": None,
            "is_completed": False,
            "completed_at": None,
            "attendees": [],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

    async def fake_schedule(self, event_id):  # type: ignore[no-untyped-def]
        return None

    async def fake_update(self, activity_id, company_id, schema):  # type: ignore[no-untyped-def]
        return None

    async def fake_toggle(self, activity_id, company_id):  # type: ignore[no-untyped-def]
        return None

    async def fake_delete(self, activity_id, company_id):  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(calendar_endpoints.CalendarService, "get_activities", fake_get_activities)
    monkeypatch.setattr(calendar_endpoints.CalendarService, "create_event", fake_create_event)
    monkeypatch.setattr(calendar_endpoints.CalendarService, "create_task", fake_create_task)
    monkeypatch.setattr(calendar_endpoints.CalendarService, "schedule_reminder", fake_schedule)
    monkeypatch.setattr(calendar_endpoints.CalendarService, "update_activity", fake_update)
    monkeypatch.setattr(calendar_endpoints.CalendarService, "toggle_task_status", fake_toggle)
    monkeypatch.setattr(calendar_endpoints.CalendarService, "delete_activity", fake_delete)

    now = datetime.utcnow()
    list_resp = await client.get("/api/calendar/", params={"start": now.isoformat(), "end": (now + timedelta(days=1)).isoformat()})
    event_resp = await client.post(
        "/api/calendar/event",
        json={
            "title": "Event",
            "description": None,
            "color": "#3788d8",
            "all_day": False,
            "start_at": now.isoformat(),
            "end_at": (now + timedelta(hours=1)).isoformat(),
            "location": None,
            "attendee_ids": [],
            "case_id": None,
            "client_id": None,
            "type": "event",
        },
    )
    task_resp = await client.post(
        "/api/calendar/task",
        json={
            "title": "Task",
            "description": None,
            "color": "#3788d8",
            "all_day": False,
            "start_at": now.isoformat(),
            "end_at": None,
            "case_id": None,
            "client_id": None,
            "type": "task",
        },
    )
    update_resp = await client.patch(f"/api/calendar/{activity_id}", json={"title": "upd"})
    toggle_resp = await client.post(f"/api/calendar/{activity_id}/toggle")
    delete_resp = await client.delete(f"/api/calendar/{activity_id}")

    assert list_resp.status_code == status.HTTP_200_OK
    assert event_resp.status_code == status.HTTP_201_CREATED
    assert task_resp.status_code == status.HTTP_201_CREATED
    assert update_resp.status_code == status.HTTP_404_NOT_FOUND
    assert toggle_resp.status_code == status.HTTP_404_NOT_FOUND
    assert delete_resp.status_code == status.HTTP_404_NOT_FOUND

    current_user.role = UserRole.ADMIN
    only_mine_resp = await client.get(
        "/api/calendar/", params={"start": now.isoformat(), "end": (now + timedelta(days=1)).isoformat(), "only_mine": True}
    )
    assert only_mine_resp.status_code == status.HTTP_200_OK
