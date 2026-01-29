from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.database.session import get_db
from src.app.services.company.models import Company
from src.app.services.company.schemas import CompanyRegister, CompanyResponse
from src.app.services.company.service import CompanyService
from src.app.services.user.models import User

router = APIRouter(prefix="/api/companies", tags=["Companies"])


@router.post("/register", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED)
async def register_company(
    payload: CompanyRegister,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Company:
    company_service = CompanyService(db)

    company, session_id = await company_service.register_new_company(payload)

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400,  # 24 часа
    )

    return company


@router.get("/me", response_model=CompanyResponse)
async def get_my_company(current_user: User = Depends(get_current_user)) -> Company:
    """
    Получение данных о компании, к которой принадлежит текущий пользователь.
    """
    if not current_user.company:
        raise HTTPException(status_code=404, detail="Компания не найдена")

    return current_user.company


# @router.patch("/me", response_model=CompanyResponse)
# async def update_my_company(payload: CompanyUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
#     """
#     Обновление данных своей компании (доступно только админам/CEO).
#     """
#     if current_user.role not in ["admin", "ceo"]:
#         raise HTTPException(status_code=403, detail="Недостаточно прав для изменения данных компании")

#     company_service = CompanyService(db)
#     updated_company = await company_service.update_company(current_user.company_id, payload)

#     return updated_company
