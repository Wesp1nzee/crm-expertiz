import json
import secrets
from collections.abc import Awaitable
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis

from src.app.core.auth.models import CachedSessionData, UserSessionData
from src.app.services.user.models import User
from src.app.services.user.schemas import UserUpdate


class SessionManager:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.session_prefix = "session:"
        self.user_index_prefix = "user_sessions:"
        self.expire_seconds = 604_800  # 1 неделя

    async def create_session(self, user: User) -> str:
        """
        Создает сессию в Redis и регистрирует её в индексе пользователя.
        """
        session_id = secrets.token_urlsafe(32)
        session_key = f"{self.session_prefix}{session_id}"
        user_index_key = f"{self.user_index_prefix}{user.id}"

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

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.setex(session_key, self.expire_seconds, json.dumps(session_data, default=str))
            pipe.sadd(user_index_key, session_id)
            pipe.expire(user_index_key, self.expire_seconds + 3600)
            await pipe.execute()

        return session_id

    async def update_user_sessions(self, user_id: UUID, is_active: bool, update_data: UserUpdate) -> None:
        """
        Мгновенно обновляет данные пользователя во всех его активных сессиях.
        """
        user_index_key = f"{self.user_index_prefix}{user_id}"
        session_ids = await cast(Awaitable[set[Any]], self.redis.smembers(user_index_key))

        if not session_ids:
            return

        if not is_active:
            async with self.redis.pipeline(transaction=True) as pipe:
                for s_id_bytes in session_ids:
                    s_id = s_id_bytes.decode() if isinstance(s_id_bytes, bytes) else s_id_bytes
                    pipe.delete(f"{self.session_prefix}{s_id}")
                pipe.delete(user_index_key)
                await pipe.execute()
            return

        updates = update_data.model_dump(exclude_unset=True)
        if not updates:
            return

        for s_id_bytes in session_ids:
            s_id = s_id_bytes.decode() if isinstance(s_id_bytes, bytes) else s_id_bytes
            session_key = f"{self.session_prefix}{s_id}"

            raw_data = await self.redis.get(session_key)
            if not raw_data:
                self.redis.srem(user_index_key, s_id)
                continue

            session_obj = CachedSessionData.model_validate_json(raw_data)
            user_dict = session_obj.user.model_dump()

            for field, value in updates.items():
                if field in user_dict:
                    user_dict[field] = value

            session_obj.user = UserSessionData(**user_dict)
            session_obj.user.is_active = is_active

            ttl = await self.redis.ttl(session_key)
            if ttl > 0:
                await self.redis.setex(session_key, ttl, session_obj.model_dump_json())

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """
        Получает данные сессии из Redis.

        Returns:
            dict: Данные сессии в формате словаря для валидации в deps.py.
        """
        data = await self.redis.get(f"{self.session_prefix}{session_id}")
        if not data:
            return None
        try:
            return cast(dict[str, Any], json.loads(data))
        except json.JSONDecodeError:
            await self.delete_session(session_id)
            return None

    async def delete_session(self, session_id: str) -> None:
        """
        Удаляет конкретную сессию и очищает её из индекса пользователя.
        """
        session_key = f"{self.session_prefix}{session_id}"
        data = await self.redis.get(session_key)

        if data:
            try:
                session_dict = json.loads(data)
                user_id = session_dict.get("user_id")
                if user_id:
                    await cast(Awaitable[int], self.redis.srem(f"{self.user_index_prefix}{user_id}", session_id))
            except Exception:
                pass

        await self.redis.delete(session_key)

    async def refresh_session(self, session_id: str) -> bool:
        """
        Обновляет TTL сессии в Redis (продление сессии).
        """
        key = f"{self.session_prefix}{session_id}"
        # Используем bool(...) чтобы явно привести Any к нужному типу возврата
        result = await cast(Awaitable[Any], self.redis.expire(key, self.expire_seconds))
        return bool(result)
