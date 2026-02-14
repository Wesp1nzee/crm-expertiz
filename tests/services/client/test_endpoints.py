import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from starlette import status

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
async def test_create_client_forbidden_for_expert(client: AsyncClient, set_current_user: Callable[[UserRole], None]) -> None:
    set_current_user(UserRole.EXPERT)

    response = await client.post(
        "/api/clients",
        json={"name": "Клиент", "type": "individual"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


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
