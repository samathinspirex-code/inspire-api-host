from typing import Callable

import jwt
from fastapi import Depends, Request

from app.core.errors import ForbiddenError, UnauthorizedError
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.security import decode_access_token


async def get_current_user(request: Request) -> CurrentUser:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise UnauthorizedError("Missing or invalid Authorization header")

    token = auth_header[len("Bearer ") :]
    try:
        claims = decode_access_token(token)
    except jwt.InvalidTokenError:
        raise UnauthorizedError("Invalid or expired token")

    access = list(claims.get("access", []))
    selected_role = request.headers.get("X-LMS-Active-Role", "").strip().upper()
    lms_roles = {"SUPER_ADMIN", "ADMIN", "LECTURER", "STUDENT"}
    if selected_role:
        if selected_role not in lms_roles or selected_role not in access:
            raise ForbiddenError("The selected LMS role is not available for this account")
        # Treat the chosen portal as the active role for this request. Other
        # assigned roles remain in the token and can be selected again later.
        access = [value for value in access if value not in lms_roles or value == selected_role]
    return CurrentUser(user_id=int(claims["sub"]), email=claims["email"], access=access)


def require_access(access_key: str) -> Callable:
    async def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if access_key not in current_user.access:
            raise ForbiddenError(f"Requires '{access_key}' access")
        return current_user

    return _dependency
