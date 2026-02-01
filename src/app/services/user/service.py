from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.auth.security import hash_password, verify_password
from src.app.services.case.models import Case
from src.app.services.user.models import User, UserEmailConfig
from src.app.services.user.schemas import (
    ROLE_PERMISSIONS,
    UserCreate,
    UserFilterParams,
    UserLoginSchema,
)


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def authenticate(self, credentials: UserLoginSchema) -> User | None:
        query = select(User).options(selectinload(User.company)).where(User.email == credentials.email)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()
        if not user:
            return None
        if not verify_password(credentials.password, user.hashed_password):
            return None
        return user

    async def set_online_status(self, user: User, is_online: bool) -> None:
        db_user = await self.db.get(User, user.id)
        if not db_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден при попытке обновить статус.")

        db_user.is_active = is_online

        if is_online:
            db_user.last_login = datetime.now(UTC)

        await self.db.commit()

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
            company_id=creator.company_id,
            is_active=True,
        )

        self.db.add(new_user)
        await self.db.flush()

        if user_in.email_config:
            email_cfg = UserEmailConfig(**user_in.email_config.model_dump(), user_id=new_user.id)
            self.db.add(email_cfg)

        await self.db.commit()
        await self.db.refresh(new_user)
        return new_user

    async def get_users_list(self, current_user: User, params: UserFilterParams) -> list[dict[Any, Any]]:
        allowed_roles = ROLE_PERMISSIONS.get(current_user.role, [])

        case_count_subquery = (
            select(func.count(Case.id))
            .where(Case.assigned_user_id == User.id)
            .where(Case.deleted_at.is_(None))
            .scalar_subquery()
            .label("count_case")
        )

        query = (
            select(User, func.coalesce(case_count_subquery, 0).label("count_case"))
            .where(User.company_id == current_user.company_id)
            .where(User.role.in_(allowed_roles))
            .where(User.id != current_user.id)
        )

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

        query = query.offset((params.page - 1) * params.limit).limit(params.limit)

        result = await self.db.execute(query)
        rows = result.all()

        users_list = []
        for user_obj, count_case in rows:
            users_list.append(
                {
                    "id": user_obj.id,
                    "email": user_obj.email,
                    "full_name": user_obj.full_name,
                    "role": user_obj.role,
                    "specialization": user_obj.specialization,
                    "is_active": user_obj.is_active,
                    "can_authenticate": user_obj.can_authenticate,
                    "company_id": user_obj.company_id,
                    "settings": user_obj.settings,
                    "created_at": user_obj.created_at,
                    "updated_at": user_obj.updated_at,
                    "last_login": user_obj.last_login,
                    "count_case": count_case or 0,
                }
            )

        return users_list

    async def update_access(self, user_id: str, can_auth: bool) -> User:
        user = await self.db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        user.can_authenticate = can_auth
        await self.db.commit()
        return user

    async def search_name(self, query: str, company_id: UUID) -> list[User]:
        stmt = (
            select(User.id, User.full_name)
            .where(
                User.company_id == company_id,
                or_(func.lower(User.full_name).startswith(func.lower(query)), func.lower(User.full_name).contains(func.lower(query))),
            )
            .limit(5)
        )

        result = await self.db.execute(stmt)
        rows = result.all()

        user_ids = [row[0] for row in rows]
        if not user_ids:
            return []

        users_stmt = select(User).where(User.id.in_(user_ids))
        users_result = await self.db.execute(users_stmt)
        return list(users_result.scalars().all())
