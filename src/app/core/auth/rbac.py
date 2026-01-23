from fastapi import Depends, HTTPException, status

from src.app.core.auth.deps import get_current_user
from src.app.services.user.models import User, UserRole
from src.app.services.user.schemas import ROLE_PERMISSIONS


class RoleChecker:
    def __init__(self, allowed_roles: list[UserRole]) -> None:
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"Недостаточно прав. Требуемые роли: {[r.value for r in self.allowed_roles]}"
            )

        if not current_user.can_authenticate:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ваш аккаунт заблокирован")

        return current_user


def check_hierarchy(creator_role: UserRole, target_role: UserRole) -> bool:
    allowed_to_manage = ROLE_PERMISSIONS.get(creator_role, [])
    return target_role in allowed_to_manage
