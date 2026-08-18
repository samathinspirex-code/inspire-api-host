from collections.abc import Callable

from fastapi import Depends

from app.core.errors import ForbiddenError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.lms.service import resolve_role


def require_lms_roles(*allowed_roles: str) -> Callable:
    async def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "LMS" not in current_user.access:
            raise ForbiddenError("LMS access is required")

        role = resolve_role(current_user.access)
        if role is None or (allowed_roles and role not in allowed_roles):
            raise ForbiddenError("Your LMS role cannot perform this action")
        return current_user

    return _dependency
