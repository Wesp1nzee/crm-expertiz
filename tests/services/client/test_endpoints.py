import pytest
from httpx import AsyncClient
from starlette import status


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
    assert "id" in data


@pytest.mark.asyncio
async def test_get_clients_list(client: AsyncClient) -> None:
    await client.post("/api/clients", json={"name": "Второй Клиент", "type": "individual"})

    response = await client.get("/api/clients")

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) >= 1


@pytest.mark.asyncio
async def test_get_client_not_found(client: AsyncClient) -> None:
    import uuid

    random_uuid = str(uuid.uuid4())
    response = await client.get(f"/api/clients/{random_uuid}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
