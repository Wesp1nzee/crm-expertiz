import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.security import hash_password
from src.app.core.config.settings import settings
from src.app.services.company.models import Company
from src.app.services.user.models import User, UserRole

logger = logging.getLogger(__name__)


async def create_first_admin(db: AsyncSession) -> None:
    try:
        query_comp = select(Company).where(Company.inn == "0000000000")
        result_comp = await db.execute(query_comp)
        system_company = result_comp.scalar_one_or_none()

        if not system_company:
            system_company = Company(name="SYSTEM_INTERNAL", inn="0000000000", balance=0)
            db.add(system_company)
            await db.flush()

        query_user = select(User).where(User.email == settings.ADMIN_EMAIL)
        result_user = await db.execute(query_user)
        existing_user = result_user.scalar_one_or_none()

        if existing_user:
            logger.info(f"Пользователь {settings.ADMIN_EMAIL} уже существует, пропуск создания.")
            return

        new_admin = User(
            email=settings.ADMIN_EMAIL,
            hashed_password=hash_password(settings.ADMIN_PASSWORD),
            full_name=settings.ADMIN_FULL_NAME,
            role=UserRole.ADMIN,
            can_authenticate=True,
            is_active=True,
            settings={"theme": "dark"},
            company_id=system_company.id,
        )

        db.add(new_admin)
        await db.commit()
        logger.info(f"Администратор {settings.ADMIN_EMAIL} успешно создан в компании {system_company.name}")

    except IntegrityError as e:
        await db.rollback()
        logger.error(f"Database Integrity Error during admin creation: {e}")
    except Exception as e:
        await db.rollback()
        logger.exception(f"Unexpected error during admin creation: {e}")
        raise
