from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.activity_audit import record_request_change
from app.core.config import settings
from app.core.errors import APIError, api_error_handler, request_validation_handler
from app.modules.auth.router import router as auth_router
from app.modules.academic.router import cms_router as academic_cms_router
from app.modules.academic.router import lms_router as academic_lms_router
from app.modules.academic.router import public_router as academic_public_router
from app.modules.cms.router import router as cms_router
from app.modules.cms.public_router import router as public_cms_router
from app.modules.cms.public_news_router import router as public_news_router
from app.modules.cms.testimonials import cms_router as testimonials_cms_router, public_router as testimonials_public_router
from app.modules.crm.router import router as crm_router
from app.modules.crm.public_router import router as crm_public_router
from app.modules.lms.router import router as lms_router
from app.modules.site_assistant.router import router as site_assistant_router
from app.modules.user_management.router import router as user_management_router

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"https://([a-z0-9-]+\.)?inspirecollege\.lk",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_administrative_changes(request, call_next):
    response = await call_next(request)
    await record_request_change(request, response.status_code)
    return response

app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(academic_public_router)
app.include_router(academic_cms_router)
app.include_router(academic_lms_router)
app.include_router(cms_router)
app.include_router(public_cms_router)
app.include_router(public_news_router)
app.include_router(testimonials_cms_router)
app.include_router(testimonials_public_router)
app.include_router(user_management_router)
app.include_router(lms_router)
app.include_router(crm_router, prefix="/api/v1")
app.include_router(crm_router, prefix="/api/v1/cms")
app.include_router(crm_public_router)
app.include_router(site_assistant_router)

