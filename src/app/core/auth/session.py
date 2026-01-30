import json
import secrets
from typing import Any

from redis.asyncio import Redis

from src.app.services.user.models import User


class SessionManager:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.session_prefix = "session:"
        self.expire_seconds = 60 * 60 * 24 * 7  # 1 неделя

    async def create_session(self, user: User) -> str:
        session_id = secrets.token_urlsafe(32)
        key = f"{self.session_prefix}{session_id}"

        user_data = {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
            "is_active": user.is_active,
            "can_authenticate": user.can_authenticate,
            "specialization": user.specialization,
        }

        company_data = None
        if user.company:
            company_data = {
                "id": str(user.company.id),
                "name": user.company.name,
                "is_active": user.company.is_active,
            }

        session_data = {
            "user_id": str(user.id),
            "user": user_data,
            "company": company_data,
        }

        await self.redis.setex(key, self.expire_seconds, json.dumps(session_data, default=str))
        return session_id

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        data = await self.redis.get(f"{self.session_prefix}{session_id}")
        if not data:
            return None
        try:
            result: dict[str, Any] = json.loads(data)
            return result
        except json.JSONDecodeError:
            await self.redis.delete(f"{self.session_prefix}{session_id}")
            return None

    async def delete_session(self, session_id: str) -> None:
        await self.redis.delete(f"{self.session_prefix}{session_id}")
