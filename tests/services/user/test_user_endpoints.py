from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from src.app.services.user import endpoints as user_endpoints
from src.app.services.user.models import UserRole


@pytest.mark.asyncio
async def test_suggest_users_and_validation(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(self, q, company_id):  # type: ignore[no-untyped-def]
        return [SimpleNamespace(id=uuid4(), full_name="User Name")]

    monkeypatch.setattr(user_endpoints.UserService, "search_name", fake_search)

    ok = await client.get("/api/users/suggest", params={"q": "Us"})
    bad = await client.get("/api/users/suggest", params={"q": "U"})

    assert ok.status_code == status.HTTP_200_OK
    assert ok.json()[0]["name"] == "User Name"
    assert bad.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis():  # type: ignore[no-untyped-def]
        return object()

    async def fake_get_session(self, sid):  # type: ignore[no-untyped-def]
        return None

    async def fake_create_session(self, user):  # type: ignore[no-untyped-def]
        return "new-session"

    async def fake_authenticate(self, credentials):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=uuid4(),
            email=credentials.email,
            full_name="Admin User",
            role=UserRole.ADMIN,
            specialization=None,
            is_active=True,
            can_authenticate=True,
            company_id=uuid4(),
            settings={},
        )

    async def fake_set_online(self, user, online):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(user_endpoints, "get_redis_client", fake_get_redis)
    monkeypatch.setattr(user_endpoints.SessionManager, "get_session", fake_get_session)
    monkeypatch.setattr(user_endpoints.SessionManager, "create_session", fake_create_session)
    monkeypatch.setattr(user_endpoints.UserService, "authenticate", fake_authenticate)
    monkeypatch.setattr(user_endpoints.UserService, "set_online_status", fake_set_online)

    response = await client.post("/api/users/login", json={"email": "admin@test.com", "password": "verysecure123"})

    assert response.status_code == status.HTTP_200_OK
    assert response.cookies.get("session_id") == "new-session"


@pytest.mark.asyncio
async def test_login_already_authorized(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis():  # type: ignore[no-untyped-def]
        return object()

    async def fake_get_session(self, sid):  # type: ignore[no-untyped-def]
        return {"ok": True}

    monkeypatch.setattr(user_endpoints, "get_redis_client", fake_get_redis)
    monkeypatch.setattr(user_endpoints.SessionManager, "get_session", fake_get_session)

    response = await client.post(
        "/api/users/login",
        json={"email": "admin@test.com", "password": "verysecure123"},
        cookies={"session_id": "old"},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis():  # type: ignore[no-untyped-def]
        return object()

    async def fake_get_session(self, sid):  # type: ignore[no-untyped-def]
        return None

    async def fake_authenticate(self, credentials):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(user_endpoints, "get_redis_client", fake_get_redis)
    monkeypatch.setattr(user_endpoints.SessionManager, "get_session", fake_get_session)
    monkeypatch.setattr(user_endpoints.UserService, "authenticate", fake_authenticate)

    response = await client.post("/api/users/login", json={"email": "admin@test.com", "password": "wrong"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_logout_me_create_list_delete_update_user(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis():  # type: ignore[no-untyped-def]
        return object()

    async def fake_delete_session(self, sid):  # type: ignore[no-untyped-def]
        return None

    async def fake_set_online(self, user, online):  # type: ignore[no-untyped-def]
        return None

    created_id = uuid4()

    async def fake_create_user(self, creator, user_in):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=created_id,
            email=user_in.email,
            full_name=user_in.full_name,
            role=user_in.role,
            specialization=user_in.specialization,
            is_active=True,
            can_authenticate=True,
            company_id=creator.company_id,
            settings={},
        )

    async def fake_get_users(self, current_user, params):  # type: ignore[no-untyped-def]
        return [
            {
                "id": str(created_id),
                "email": "u@test.com",
                "full_name": "User",
                "role": "expert",
                "specialization": None,
                "is_active": True,
                "can_authenticate": True,
                "company_id": str(current_user.company_id),
                "settings": {},
                "count_case": 0,
            }
        ]

    async def fake_delete_user(self, user_id):  # type: ignore[no-untyped-def]
        return None

    async def fake_update_user(self, user_id, case_data, role):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(user_endpoints, "get_redis_client", fake_get_redis)
    monkeypatch.setattr(user_endpoints.SessionManager, "delete_session", fake_delete_session)
    monkeypatch.setattr(user_endpoints.UserService, "set_online_status", fake_set_online)
    monkeypatch.setattr(user_endpoints.UserService, "create_user", fake_create_user)
    monkeypatch.setattr(user_endpoints.UserService, "get_users_list", fake_get_users)
    monkeypatch.setattr(user_endpoints.UserService, "delete_user", fake_delete_user)
    monkeypatch.setattr(user_endpoints.UserService, "update_user", fake_update_user)

    logout_resp = await client.post("/api/users/logout", cookies={"session_id": "to-delete"})
    me_resp = await client.get("/api/users/me")
    create_resp = await client.post(
        "/api/users",
        json={
            "email": "new@test.com",
            "full_name": "New User",
            "role": "expert",
            "password": "verysecure123",
            "specialization": None,
            "is_active": True,
            "settings": {},
        },
    )
    list_resp = await client.get("/api/users")
    delete_resp = await client.delete(f"/api/users/{created_id}")
    patch_resp = await client.patch(f"/api/users/{created_id}", json={"full_name": "Changed"})

    assert logout_resp.status_code == status.HTTP_200_OK
    assert me_resp.status_code == status.HTTP_200_OK
    assert create_resp.status_code == status.HTTP_201_CREATED
    assert list_resp.status_code == status.HTTP_200_OK
    assert delete_resp.status_code == status.HTTP_200_OK
    assert patch_resp.status_code == status.HTTP_204_NO_CONTENT
