from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth import service
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import (
    CurrentUser,
    AuthenticatorLoginRequest,
    AuthenticatorRecoveryRequest,
    AuthenticatorSetupCompleteRequest,
    AuthenticatorSetupCompleteResponse,
    AuthenticatorSetupStartRequest,
    AuthenticatorSetupStartResponse,
    ExchangeSsoTicketRequest,
    LogoutRequest,
    RefreshRequest,
    SsoTicketResponse,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.post(
    "/auth/authenticator/setup/start",
    response_model=AuthenticatorSetupStartResponse,
)
async def start_authenticator_setup(
    payload: AuthenticatorSetupStartRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthenticatorSetupStartResponse:
    return await service.start_authenticator_setup(db, payload.email, payload.setup_token)


@router.post(
    "/auth/authenticator/setup/complete",
    response_model=AuthenticatorSetupCompleteResponse,
)
async def complete_authenticator_setup(
    payload: AuthenticatorSetupCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthenticatorSetupCompleteResponse:
    client_ip = request.client.host if request.client else "unknown"
    return await service.complete_authenticator_setup(
        db, payload.email, payload.setup_token, payload.code, client_ip
    )


@router.post("/auth/authenticator/verify", response_model=TokenResponse)
async def verify_authenticator(
    payload: AuthenticatorLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    return await service.verify_authenticator(db, payload.email, payload.code, client_ip)


@router.post("/auth/authenticator/recovery", response_model=TokenResponse)
async def verify_authenticator_recovery(
    payload: AuthenticatorRecoveryRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    return await service.verify_recovery_code(
        db, payload.email, payload.recovery_code, client_ip
    )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    return await service.refresh_tokens(db, payload.refresh_token)


@router.post("/auth/logout", status_code=204)
async def logout(
    payload: LogoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.logout(db, payload.refresh_token)


@router.get("/me", response_model=UserOut)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> UserOut:
    return await service.get_me(db, current_user.user_id)


@router.post("/auth/sso-ticket", response_model=SsoTicketResponse)
async def create_sso_ticket(
    current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> SsoTicketResponse:
    return await service.create_sso_ticket(db, current_user.user_id)


@router.post("/auth/exchange-sso-ticket", response_model=TokenResponse)
async def exchange_sso_ticket(
    payload: ExchangeSsoTicketRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    return await service.exchange_sso_ticket(db, payload.ticket)
