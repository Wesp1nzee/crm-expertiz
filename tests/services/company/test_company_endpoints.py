from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from src.app.services.company import endpoints as company_endpoints


@pytest.mark.asyncio
async def test_register_company_sets_cookie(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_register(self, payload):  # type: ignore[no-untyped-def]
        return (
            SimpleNamespace(
                id=uuid4(),
                name=payload.name,
                inn=payload.inn,
                email=payload.email,
                phone=None,
                address=None,
                balance="0.00",
                is_active=True,
                created_at="2024-01-01T00:00:00",
            ),
            "session-token",
        )

    monkeypatch.setattr(company_endpoints.CompanyService, "register_new_company", fake_register)

    response = await client.post(
        "/api/companies/register",
        json={
            "name": "Test Co",
            "inn": "1234567890",
            "email": "owner@test.com",
            "password": "verysecure123",
            "full_name": "Owner",
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.cookies.get("session_id") == "session-token"


@pytest.mark.asyncio
async def test_get_my_company_success(client: AsyncClient, current_user: SimpleNamespace) -> None:
    current_user.company = SimpleNamespace(
        id=uuid4(),
        name="Comp",
        inn="1234567890",
        email="c@test.com",
        phone=None,
        address=None,
        balance="0.00",
        is_active=True,
        created_at="2024-01-01T00:00:00",
    )

    response = await client.get("/api/companies/me")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["name"] == "Comp"


@pytest.mark.asyncio
async def test_get_my_company_not_found(client: AsyncClient, current_user: SimpleNamespace) -> None:
    current_user.company = None

    response = await client.get("/api/companies/me")

    assert response.status_code == status.HTTP_404_NOT_FOUND
