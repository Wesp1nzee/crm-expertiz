from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.app.core.database import all_models  # noqa: F401
from src.app.core.database.session import AsyncSessionLocal, engine
from src.app.core.redis import get_redis_client
from src.app.core.storage.s3 import s3_storage
from src.app.services.calendar.endpoints import router as calendar_router
from src.app.services.case.endpoints import router as cases_router
from src.app.services.client.endpoints import router as client_router
from src.app.services.company.endpoints import router as company_router
from src.app.services.document.endpoints import router as document_router
from src.app.services.user.endpoints import router as user_router
from src.app.services.user.setup import create_first_admin


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    print("Starting system health checks...")

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("PostgreSQL connection: OK")
    except Exception as e:
        print(f"PostgreSQL connection: FAILED | {e}")
        raise e

    try:
        redis_client = await get_redis_client()
        await cast(Awaitable[Any], redis_client.ping())
        print("Redis connection: OK")
    except Exception as e:
        print(f"Redis connection: FAILED | {e}")

    try:
        await s3_storage.init_bucket()
        print("S3 Storage initialization: OK")
    except Exception as e:
        print(f"S3 Storage initialization: FAILED | {e}")

    try:
        async with AsyncSessionLocal() as session:
            await create_first_admin(session)
        print("Admin initialization check: OK")
    except Exception as e:
        print(f"Admin initialization: FAILED | {e}")

    print("Application is ready to serve requests.")

    yield

    print("Shutting down application...")
    await engine.dispose()
    print("Cleanup complete.")


app = FastAPI(title="CRM Expertiz API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(cases_router)
app.include_router(client_router)
app.include_router(document_router)
app.include_router(user_router)
app.include_router(company_router)
app.include_router(calendar_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
