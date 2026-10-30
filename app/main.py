from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.public_campus import router as public_campus_router
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
from app.modules.crm.integration_router import router as crm_integration_router
from app.modules.crm.public_router import router as crm_public_router
from app.modules.lms.router import router as lms_router
from app.modules.site_assistant.router import router as site_assistant_router
from app.modules.user_management.router import router as user_management_router
from app.modules.payments.router import router as payments_router, public_router as payments_public_router
from app.modules.insights.router import router as insights_router
from app.modules.partners.router import router as partners_router
from app.modules.partners.public_router import router as partner_public_router
from app.modules.connectors.router import router as connectors_router
from app.modules.ai_copilot.router import router as copilot_router
from app.modules.payments.db import close_pool as close_payments_pool

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await close_payments_pool()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

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
app.include_router(public_campus_router)
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
app.include_router(crm_integration_router)
app.include_router(site_assistant_router)
app.include_router(payments_router)
app.include_router(payments_public_router)
app.include_router(insights_router)
app.include_router(partners_router)
app.include_router(partner_public_router)
app.include_router(connectors_router)
app.include_router(copilot_router)

