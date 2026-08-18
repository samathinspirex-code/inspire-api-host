from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.config import settings
from app.core.errors import APIError, api_error_handler, request_validation_handler
from app.modules.auth.router import router as auth_router
from app.modules.cms.router import router as cms_router
from app.modules.cms.public_router import router as public_cms_router
from app.modules.cms.public_news_router import router as public_news_router
from app.modules.lms.router import router as lms_router
from app.modules.user_management.router import router as user_management_router

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(cms_router)
app.include_router(public_cms_router)
app.include_router(public_news_router)
app.include_router(user_management_router)
app.include_router(lms_router)

