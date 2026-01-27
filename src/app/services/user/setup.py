from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.security import hash_password
from src.app.core.config.settings import settings
from src.app.services.company.models import Company
from src.app.services.user.models import User, UserRole


async def create_first_admin(db: AsyncSession) -> None:
    query_comp = select(Company).where(Company.inn == "0000000000")
    result_comp = await db.execute(query_comp)
    system_company = result_comp.scalar_one_or_none()

    if not system_company:
        system_company = Company(name="SYSTEM_INTERNAL", inn="0000000000", balance=0)
        db.add(system_company)
        await db.flush()

    query_user = select(User).where(User.role == UserRole.ADMIN)
    result_user = await db.execute(query_user)
    admin_exists = result_user.scalar_one_or_none()

    if not admin_exists:
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

        try:
            await db.commit()
            print(f"Администратор создан в системе {system_company.name}")
        except Exception as e:
            await db.rollback()
            print(f"Ошибка: {e}")
