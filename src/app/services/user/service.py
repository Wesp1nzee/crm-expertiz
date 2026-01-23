from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.security import hash_password, verify_password
from src.app.core.auth.session import SessionManager
from src.app.services.user.models import User, UserEmailConfig
from src.app.services.user.schemas import (
    ROLE_PERMISSIONS,
    UserCreate,
    UserFilterParams,
    UserLoginSchema,
)


class UserService:
    def __init__(self, db: AsyncSession, session_manager: SessionManager | None = None) -> None:
        self.db = db
        self.session_manager = session_manager

    async def authenticate(self, credentials: UserLoginSchema) -> User | None:
        query = select(User).where(User.email == credentials.email)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            return None

        if not verify_password(credentials.password, user.hashed_password):
            return None

        return user

    async def set_online_status(self, user: User, is_online: bool) -> None:
        user.is_active = is_online

        if is_online:
            user.last_login = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(user)

    async def create_user(self, creator: User, user_in: UserCreate) -> User:
        if user_in.role not in ROLE_PERMISSIONS.get(creator.role, []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Вы не можете создавать пользователя с ролью {user_in.role}",
            )

        existing_user_query = select(User).where(User.email == user_in.email)
        existing_user_result = await self.db.execute(existing_user_query)
        if existing_user_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email уже зарегистрирован")

        new_user = User(
            email=user_in.email,
            hashed_password=hash_password(user_in.password),
            full_name=user_in.full_name,
            role=user_in.role,
            specialization=user_in.specialization,
            settings=user_in.settings or {},
        )

        self.db.add(new_user)
        await self.db.flush()

        if user_in.email_config:
            email_cfg = UserEmailConfig(**user_in.email_config.model_dump(), user_id=new_user.id)
            self.db.add(email_cfg)

        await self.db.commit()
        await self.db.refresh(new_user)
        return new_user

    async def get_users_list(self, current_user: User, params: UserFilterParams) -> list[User]:
        allowed_roles = ROLE_PERMISSIONS.get(current_user.role, [])
        query = select(User).where(User.role.in_(allowed_roles))

        if params.role:
            query = query.where(User.role == params.role)
        if params.is_active is not None:
            query = query.where(User.is_active == params.is_active)
        if params.search:
            query = query.where(
                or_(
                    User.full_name.ilike(f"%{params.search}%"),
                    User.email.ilike(f"%{params.search}%"),
                )
            )

        sort_column = getattr(User, params.sort_by, User.created_at)
        if params.order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))

        result = await self.db.execute(query)
        users_seq: Sequence[User] = result.scalars().all()
        return list(users_seq)

    async def update_access(self, user_id: str, can_auth: bool) -> User:
        user = await self.db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        user.can_authenticate = can_auth
        await self.db.commit()
        return user
