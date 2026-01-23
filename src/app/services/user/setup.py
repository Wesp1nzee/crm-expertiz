from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.security import hash_password
from src.app.core.config.settings import settings
from src.app.services.user.models import User, UserRole


async def create_first_admin(db: AsyncSession) -> None:
    query = select(User).where(User.role == UserRole.ADMIN)
    result = await db.execute(query)
    admin_exists = result.scalar_one_or_none()

    if not admin_exists:
        print("Инициализация: Администратор не найден.")
        new_admin = User(
            email=settings.ADMIN_EMAIL,
            hashed_password=hash_password(settings.ADMIN_PASSWORD),
            full_name=settings.ADMIN_FULL_NAME,
            role=UserRole.ADMIN,
            can_authenticate=True,
            is_active=False,
            settings={"theme": "dark", "notifications": True},
        )

        db.add(new_admin)
        try:
            await db.commit()
            print(f"Администратор {settings.ADMIN_EMAIL} успешно создан.")
        except Exception as e:
            await db.rollback()
            print(f"Ошибка при создании админа: {e}")
    else:
        print("Инициализация: Администратор уже существует.")
