import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from starlette import status

from src.app.services.client import endpoints as client_endpoints
from src.app.services.user.models import UserRole


@pytest.mark.asyncio
async def test_create_client(client: AsyncClient) -> None:
    client_data = {
        "name": "ООО Тестовая Компания",
        "short_name": "Тест",
        "type": "legal",
        "inn": "1234567890",
        "email": "test@example.com",
        "phone": "+79991234567",
    }

    response = await client.post("/api/clients", json=client_data)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == client_data["name"]
    assert data["inn"] == client_data["inn"]
    assert "id" in data


@pytest.mark.asyncio
async def test_create_client_integrity_and_unexpected_error(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_integrity(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise IntegrityError("stmt", {}, Exception("x"))

    async def raise_unexpected(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(client_endpoints.ClientService, "create_client", raise_integrity)
    r1 = await client.post("/api/clients", json={"name": "Клиент", "type": "individual"})
    monkeypatch.setattr(client_endpoints.ClientService, "create_client", raise_unexpected)
    r2 = await client.post("/api/clients", json={"name": "Клиент", "type": "individual"})

    assert r1.status_code == status.HTTP_400_BAD_REQUEST
    assert r2.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_create_client_forbidden_for_expert(client: AsyncClient, set_current_user: Callable[[UserRole], None]) -> None:
    set_current_user(UserRole.EXPERT)

    response = await client.post(
        "/api/clients",
        json={"name": "Клиент", "type": "individual"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_get_clients_internal_error(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_error(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(client_endpoints.ClientService, "get_clients", raise_error)
    response = await client.get("/api/clients")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_get_clients_list_with_metadata(client: AsyncClient) -> None:
    await client.post("/api/clients", json={"name": "Второй Клиент", "type": "individual"})

    response = await client.get("/api/clients")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["total"] >= 1
    assert payload["page"] == 1
    assert isinstance(payload["items"], list)


@pytest.mark.asyncio
async def test_get_client_by_id(client: AsyncClient) -> None:
    create_response = await client.post("/api/clients", json={"name": "Точечный Клиент", "type": "individual"})
    client_id = create_response.json()["id"]

    response = await client.get(f"/api/clients/{client_id}")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == client_id


@pytest.mark.asyncio
async def test_get_client_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/api/clients/{uuid.uuid4()}")

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_update_client(client: AsyncClient) -> None:
    create_response = await client.post("/api/clients", json={"name": "Старое имя", "type": "individual"})
    client_id = create_response.json()["id"]

    response = await client.patch(f"/api/clients/{client_id}", json={"name": "Новое имя"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["name"] == "Новое имя"


@pytest.mark.asyncio
async def test_delete_client(client: AsyncClient) -> None:
    create_response = await client.post("/api/clients", json={"name": "Удаляемый", "type": "individual"})
    client_id = create_response.json()["id"]

    delete_response = await client.delete(f"/api/clients/{client_id}")
    get_response = await client.get(f"/api/clients/{client_id}")

    assert delete_response.status_code == status.HTTP_204_NO_CONTENT
    assert get_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_suggest_clients(client: AsyncClient) -> None:
    await client.post("/api/clients", json={"name": "Альфа Консалт", "type": "legal"})
    await client.post("/api/clients", json={"name": "Бета Лигал", "type": "legal"})

    response = await client.get("/api/clients/suggest", params={"q": "Ал"})

    assert response.status_code == status.HTTP_200_OK
    suggestions = response.json()
    assert any(item["name"] == "Альфа Консалт" for item in suggestions)


@pytest.mark.asyncio
async def test_suggest_clients_query_validation(client: AsyncClient) -> None:
    response = await client.get("/api/clients/suggest", params={"q": "А"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_update_client_forbidden_not_found_integrity(
    client: AsyncClient, set_current_user: Callable[[UserRole], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_current_user(UserRole.EXPERT)
    forbidden = await client.patch(f"/api/clients/{uuid.uuid4()}", json={"name": "x"})
    set_current_user(UserRole.ADMIN)

    async def return_none(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    async def raise_integrity(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise IntegrityError("stmt", {}, Exception("x"))

    monkeypatch.setattr(client_endpoints.ClientService, "update_client", return_none)
    not_found = await client.patch(f"/api/clients/{uuid.uuid4()}", json={"name": "x"})
    monkeypatch.setattr(client_endpoints.ClientService, "update_client", raise_integrity)
    bad_request = await client.patch(f"/api/clients/{uuid.uuid4()}", json={"name": "x"})

    assert forbidden.status_code == status.HTTP_403_FORBIDDEN
    assert not_found.status_code == status.HTTP_404_NOT_FOUND
    assert bad_request.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_delete_client_forbidden_and_not_found(
    client: AsyncClient, set_current_user: Callable[[UserRole], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_current_user(UserRole.EXPERT)
    forbidden = await client.delete(f"/api/clients/{uuid.uuid4()}")
    set_current_user(UserRole.ADMIN)

    async def return_false(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return False

    monkeypatch.setattr(client_endpoints.ClientService, "delete_client", return_false)
    not_found = await client.delete(f"/api/clients/{uuid.uuid4()}")

    assert forbidden.status_code == status.HTTP_403_FORBIDDEN
    assert not_found.status_code == status.HTTP_404_NOT_FOUND
