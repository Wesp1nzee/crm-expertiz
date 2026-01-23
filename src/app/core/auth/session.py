import json
import secrets
from typing import Any

from redis.asyncio import Redis


class SessionManager:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.session_prefix = "session:"
        self.expire_seconds = 60 * 60 * 24 * 7  # 1 неделя

    async def create_session(self, user_id: str, data: dict[str, Any]) -> str:
        session_id = secrets.token_urlsafe(32)
        key = f"{self.session_prefix}{session_id}"

        session_data = {"user_id": user_id, **data}
        await self.redis.setex(key, self.expire_seconds, json.dumps(session_data))
        return session_id

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        data = await self.redis.get(f"{self.session_prefix}{session_id}")
        if not data:
            return None

        result: dict[str, Any] = json.loads(data)
        return result

    async def delete_session(self, session_id: str) -> None:
        await self.redis.delete(f"{self.session_prefix}{session_id}")
