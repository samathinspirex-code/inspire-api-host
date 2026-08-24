from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.auth.models import User
from app.modules.auth import service as auth_service
from app.modules.auth.repository import AuthenticatorRepository
from app.modules.auth.schemas import AuthenticatorInvitationResponse
from app.modules.user_management.repository import UserManagementRepository
from app.modules.user_management.schemas import UserCreate, UserDetail, UserListResponse, UserUpdate


def _to_detail(user: User, authenticator_configured: bool = False) -> UserDetail:
    access = [ual.access_level.access_key for ual in user.access_levels]
    return UserDetail(
        user_id=user.user_id,
        name=user.full_name or "",
        email=user.email,
        access=access,
        authenticator_configured=authenticator_configured,
    )


async def list_users(db: AsyncSession) -> UserListResponse:
    repo = UserManagementRepository(db)
    users = await repo.list_all()
    configured = await AuthenticatorRepository(db).configured_user_ids(
        [user.user_id for user in users]
    )
    return UserListResponse(
        data=[_to_detail(user, user.user_id in configured) for user in users]
    )


async def create_user(db: AsyncSession, payload: UserCreate) -> UserDetail:
    repo = UserManagementRepository(db)
    email = payload.email.strip().lower()

    if await repo.get_by_email(email) is not None:
        raise ConflictError(f"Email '{email}' is already in use.")

    access_levels = await repo.get_access_levels_by_keys(payload.access)
    user = await repo.create(payload.name, email, access_levels)
    return _to_detail(user)


async def get_user(db: AsyncSession, user_id: int) -> UserDetail:
    repo = UserManagementRepository(db)
    user = await repo.get(user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")

    configured = await AuthenticatorRepository(db).configured_user_ids([user.user_id])
    return _to_detail(user, user.user_id in configured)


async def update_user(db: AsyncSession, user_id: int, payload: UserUpdate) -> UserDetail:
    repo = UserManagementRepository(db)
    user = await repo.get(user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")

    email = payload.email.strip().lower()
    if await repo.get_by_email(email, exclude_user_id=user_id) is not None:
        raise ConflictError(f"Email '{email}' is already in use.")

    access_levels = await repo.get_access_levels_by_keys(payload.access)
    user = await repo.update(user, payload.name, email, access_levels)
    configured = await AuthenticatorRepository(db).configured_user_ids([user.user_id])
    return _to_detail(user, user.user_id in configured)


async def create_authenticator_setup_token(
    db: AsyncSession, user_id: int, created_by: int
) -> AuthenticatorInvitationResponse:
    return await auth_service.issue_authenticator_setup_invitation(
        db, user_id, created_by
    )


async def delete_user(db: AsyncSession, user_id: int) -> None:
    repo = UserManagementRepository(db)
    user = await repo.get(user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")

    await repo.delete(user)
