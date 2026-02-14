from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from starlette import status

from src.app.services.case import endpoints as case_endpoints
from src.app.services.user.models import UserRole


def _case_payload(client_id: str) -> dict[str, str]:
    now = datetime.utcnow()
    return {
        "client_id": client_id,
        "number": "1",
        "case_number": "A-1",
        "authority": "Court",
        "case_type": "civil",
        "object_type": "flat",
        "object_address": "Addr",
        "status": "in_work",
        "assigned_user_id": None,
        "start_date": now.isoformat(),
        "deadline": (now + timedelta(days=10)).isoformat(),
        "completion_date": None,
        "cost": "100.00",
        "bank_transfer_amount": "0.00",
        "cash_amount": "0.00",
        "remaining_debt": "0.00",
        "plaintiff": None,
        "defendant": None,
        "expert_painting": None,
        "archive_status": None,
        "remarks": None,
    }


@pytest.mark.asyncio
async def test_case_endpoints_matrix(client: AsyncClient, current_user: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = uuid4()

    async def fake_financial(self, user_id, role):  # type: ignore[no-untyped-def]
        return {
            "total_revenue": "0.00",
            "pending_payments": 0,
            "pending_amount": "0.00",
            "average_case_cost": "0.00",
            "total_cases": 1,
            "completed_cases": 0,
            "active_cases": 1,
            "overdue_cases": 0,
        }

    async def fake_suggest(self, q, uid, role):  # type: ignore[no-untyped-def]
        return [{"id": str(case_id), "number": "1", "case_number": "A-1"}]

    async def fake_get_cases(self, params, uid, role):  # type: ignore[no-untyped-def]
        return {
            "data": [],
            "pagination": {"total": 0, "page": 1, "limit": 20, "total_pages": 0},
            "summary": {"active": 0, "overdue": 0, "completed": 0},
        }

    async def fake_create(self, case_data, uid, role):  # type: ignore[no-untyped-def]
        return {
            **case_data.model_dump(),
            "id": str(case_id),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "assigned_expert": None,
        }

    async def fake_details(self, case_id, user_id, user_role):  # type: ignore[no-untyped-def]
        return None

    async def fake_update(self, case_id, case_data, role):  # type: ignore[no-untyped-def]
        return None

    async def fake_soft_delete(self, case_id, role):  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(case_endpoints.CaseService, "get_financial_summary", fake_financial)
    monkeypatch.setattr(case_endpoints.CaseService, "suggest_cases", fake_suggest)
    monkeypatch.setattr(case_endpoints.CaseService, "get_cases", fake_get_cases)
    monkeypatch.setattr(case_endpoints.CaseService, "create_case", fake_create)
    monkeypatch.setattr(case_endpoints.CaseService, "get_case_details", fake_details)
    monkeypatch.setattr(case_endpoints.CaseService, "update_case", fake_update)
    monkeypatch.setattr(case_endpoints.CaseService, "soft_delete_case", fake_soft_delete)

    summary = await client.get("/api/cases/financial-summary")
    suggest = await client.get("/api/cases/suggest", params={"q": "1"})
    get_cases = await client.get("/api/cases")
    create = await client.post("/api/cases", json=_case_payload(str(uuid4())))
    details = await client.get(f"/api/cases/{case_id}")
    update = await client.patch(f"/api/cases/{case_id}", json={"number": "2"})
    delete = await client.delete(f"/api/cases/{case_id}")

    assert summary.status_code == status.HTTP_200_OK
    assert suggest.status_code == status.HTTP_200_OK
    assert get_cases.status_code == status.HTTP_200_OK
    assert create.status_code == status.HTTP_201_CREATED
    assert details.status_code == status.HTTP_404_NOT_FOUND
    assert update.status_code == status.HTTP_404_NOT_FOUND
    assert delete.status_code == status.HTTP_404_NOT_FOUND

    current_user.role = UserRole.EXPERT
    forbidden = await client.post("/api/cases", json=_case_payload(str(uuid4())))
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_case_create_and_update_error_paths(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_integrity(self, case_data, uid, role):  # type: ignore[no-untyped-def]
        raise IntegrityError("stmt", {}, Exception("x"))

    async def raise_http(self, case_data, uid, role):  # type: ignore[no-untyped-def]
        raise HTTPException(status_code=422, detail="bad")

    async def raise_unexpected(self, case_id, case_data, role):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    payload = _case_payload(str(uuid4()))

    monkeypatch.setattr(case_endpoints.CaseService, "create_case", raise_integrity)
    r1 = await client.post("/api/cases", json=payload)
    monkeypatch.setattr(case_endpoints.CaseService, "create_case", raise_http)
    r2 = await client.post("/api/cases", json=payload)
    monkeypatch.setattr(case_endpoints.CaseService, "update_case", raise_unexpected)
    r3 = await client.patch(f"/api/cases/{uuid4()}", json={"number": "3"})

    assert r1.status_code == status.HTTP_400_BAD_REQUEST
    assert r2.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert r3.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_download_case_documents_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/api/cases/{uuid4()}/download-documents")
    assert response.status_code == status.HTTP_404_NOT_FOUND
