from fastapi import FastAPI

from src.app.services.case.endpoints import router as cases_router
from src.app.services.client.endpoints import router as client_router

app = FastAPI(title="CRM Expertiz")


app.include_router(cases_router)
app.include_router(client_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
