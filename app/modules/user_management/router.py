from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_access
from app.modules.auth.schemas import AuthenticatorInvitationResponse, CurrentUser
from app.modules.user_management import service
from app.modules.user_management.schemas import UserCreate, UserDetail, UserListResponse, UserUpdate

router = APIRouter(
    prefix="/api/v1/cms", tags=["user-management"], dependencies=[Depends(require_access("USER_MANAGEMENT"))]
)


@router.get("/users", response_model=UserListResponse)
async def list_users(db: AsyncSession = Depends(get_db)) -> UserListResponse:
    return await service.list_users(db)


@router.post("/users", response_model=UserDetail, status_code=201)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> UserDetail:
    return await service.create_user(db, payload)


@router.get("/users/{user_id}", response_model=UserDetail)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)) -> UserDetail:
    return await service.get_user(db, user_id)


@router.post(
    "/users/{user_id}/authenticator-setup",
    response_model=AuthenticatorInvitationResponse,
)
async def create_authenticator_setup_token(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AuthenticatorInvitationResponse:
    return await service.create_authenticator_setup_token(
        db, user_id, current_user.user_id
    )


@router.put("/users/{user_id}", response_model=UserDetail)
async def update_user(user_id: int, payload: UserUpdate, db: AsyncSession = Depends(get_db)) -> UserDetail:
    return await service.update_user(db, user_id, payload)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await service.delete_user(db, user_id)
