import os
import uuid
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_ACCESS_KEY", "test")
os.environ.setdefault("S3_SECRET_KEY", "test")
os.environ.setdefault("S3_BUCKET_NAME", "test")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_FULL_NAME", "Admin")
os.environ.setdefault("ADMIN_PASSWORD", "password")

from src.app.core.auth.deps import get_current_user
from src.app.core.database.base import Base
from src.app.core.database.session import get_db
from src.app.services.case.models import Case
from src.app.services.client.models import Client, Contact
from src.app.services.company.models import Company
from src.app.services.user.models import User, UserRole
from src.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_MODELS = [Company, User, Client, Contact, Case]

engine_test = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
AsyncSessionLocalTest = async_sessionmaker(engine_test, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_db() -> AsyncGenerator[None]:
    """Инициализация тестовой базы данных"""
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine_test.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """Фикстура сессии базы данных с очисткой после каждого теста"""
    async with AsyncSessionLocalTest() as session:
        for model in reversed(TEST_MODELS):
            await session.execute(delete(model))
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def current_user(db_session: AsyncSession) -> SimpleNamespace:
    """Фикстура, предоставляющая текущего пользователя для тестирования"""
    company = Company(name="Test Company", inn=f"{uuid.uuid4().int}"[:10])
    db_session.add(company)
    await db_session.commit()
    await db_session.refresh(company)

    return SimpleNamespace(
        id=uuid.uuid4(),
        company_id=company.id,
        role=UserRole.ADMIN,
    )


@pytest_asyncio.fixture
async def set_current_user(current_user: SimpleNamespace) -> Callable[[UserRole], None]:
    """Фикстура для изменения роли текущего пользователя"""

    def _set_role(role: UserRole) -> None:
        current_user.role = role

    return _set_role


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, current_user: SimpleNamespace) -> AsyncGenerator[AsyncClient]:
    """Фикстура HTTP клиента для тестирования эндпоинтов"""

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    async def override_get_current_user() -> SimpleNamespace:
        return current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
