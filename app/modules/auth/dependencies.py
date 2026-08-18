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

    return CurrentUser(user_id=int(claims["sub"]), email=claims["email"], access=claims.get("access", []))


def require_access(access_key: str) -> Callable:
    async def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if access_key not in current_user.access:
            raise ForbiddenError(f"Requires '{access_key}' access")
        return current_user

    return _dependency
