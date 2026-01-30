from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.security import hash_password
from src.app.core.auth.session import SessionManager
from src.app.core.redis import get_redis_client
from src.app.services.company.models import Company
from src.app.services.company.schemas import CompanyRegister
from src.app.services.user.models import User


class CompanyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register_new_company(self, data: CompanyRegister) -> tuple[Company, str]:
        existing_inn = await self.db.execute(select(Company).where(Company.inn == data.inn))
        if existing_inn.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Компания с таким ИНН уже зарегистрирована")

        existing_user = await self.db.execute(select(User).where(User.email == data.email))
        if existing_user.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Пользователь с таким Email уже существует")

        try:
            new_company = Company(name=data.name, inn=data.inn)
            self.db.add(new_company)
            await self.db.flush()

            new_ceo = User(
                email=data.email,
                hashed_password=hash_password(data.password),
                full_name=data.full_name,
                role="CEO",
                company_id=new_company.id,
                can_authenticate=True,
                is_active=True,
            )
            self.db.add(new_ceo)

            await self.db.commit()

            await self.db.refresh(new_company)
            await self.db.refresh(new_ceo)

            redis_client = await get_redis_client()
            session_manager = SessionManager(redis_client)

            session_id = await session_manager.create_session(new_ceo)

            return new_company, session_id

        except Exception as e:
            await self.db.rollback()
            print(f"Ошибка при регистрации: {e}")
            raise e
