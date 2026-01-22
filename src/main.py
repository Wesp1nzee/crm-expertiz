from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.app.core.database import all_models  # noqa: F401
from src.app.core.storage.s3 import s3_storage
from src.app.services.case.endpoints import router as cases_router
from src.app.services.client.endpoints import router as client_router
from src.app.services.document.endpoints import router as document_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        await s3_storage.init_bucket()
    except Exception as e:
        print(f"S3 Storage initialization failed: {e}")

    yield

    pass


app = FastAPI(title="CRM Expertiz API", lifespan=lifespan)


app.include_router(cases_router)
app.include_router(client_router)
app.include_router(document_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
