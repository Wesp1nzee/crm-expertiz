import math
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.auth.models import UserContext
from src.app.core.auth.security import hash_password, verify_password
from src.app.core.schemas import PaginatedResponse, PaginationMeta
from src.app.services.case.models import case_experts
from src.app.services.user.models import User, UserEmailConfig, UserRole
from src.app.services.user.schemas import ROLE_PERMISSIONS, UserCreate, UserFilterParams, UserLoginSchema, UserUpdate, WorkerShortResponse


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

    async def set_online_status(self, user_id: UUID, can_authenticate: bool) -> None:
        db_user = await self.db.get(User, user_id)
        if not db_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден при попытке обновить статус.")

        db_user.can_authenticate = can_authenticate

        if can_authenticate:
            db_user.last_login = datetime.now(UTC)

        await self.db.commit()

    async def create_user(self, creator: UserContext, user_in: UserCreate) -> User:
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

    async def get_users_list(self, current_user: UserContext, params: UserFilterParams) -> PaginatedResponse[WorkerShortResponse]:
        allowed_roles = ROLE_PERMISSIONS.get(current_user.role, [])

        case_count_subquery = (
            select(func.count(case_experts.c.case_id)).where(case_experts.c.user_id == User.id).scalar_subquery().label("active_cases_count")
        )

        query = (
            select(User, func.coalesce(case_count_subquery, 0).label("active_cases_count"))
            .where(User.company_id == current_user.company_id)
            .where(User.role.in_(allowed_roles))
            .where(User.id != current_user.id)
        )

        if params.role:
            query = query.where(User.role == params.role)
        if params.is_active is not None:
            query = query.where(User.is_active == params.is_active)
        if params.search:
            search_filter = f"%{params.search}%"
            query = query.where(
                or_(
                    User.full_name.ilike(search_filter),
                    User.email.ilike(search_filter),
                )
            )

        count_stmt = select(func.count()).select_from(query.subquery())
        total_items = (await self.db.execute(count_stmt)).scalar() or 0

        sort_column = getattr(User, params.sort_by, User.created_at)
        query = query.order_by(desc(sort_column) if params.order == "desc" else asc(sort_column))

        offset = (params.page - 1) * params.limit
        query = query.offset(offset).limit(params.limit)

        result = await self.db.execute(query)
        rows = result.all()

        items = []
        for user_obj, active_cases_count in rows:
            user_obj.active_cases_count = active_cases_count
            items.append(WorkerShortResponse.model_validate(user_obj))

        total_pages = math.ceil(total_items / params.limit) if total_items > 0 else 1

        meta = PaginationMeta(
            total_items=total_items,
            total_pages=total_pages,
            current_page=params.page,
            per_page=params.limit,
            has_next=params.page < total_pages,
            has_prev=params.page > 1,
        )

        return PaginatedResponse[WorkerShortResponse](items=items, meta=meta)

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

    async def delete_user(self, user_id: UUID) -> None:
        res = await self.db.get(User, user_id)
        if not res:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

        await self.db.delete(res)
        await self.db.commit()

    async def update_user(self, user_id: UUID, update_data: UserUpdate, user_role: UserRole) -> None:
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У вас нет прав для обновления пользователя.",
            )
        user = await self.db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            if hasattr(user, field):
                setattr(user, field, value)

        await self.db.commit()
        await self.db.refresh(user)
