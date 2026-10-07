"""Small, read-only counts for the public Course Studio sign-in screen."""

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.models import User
from app.modules.cms.models import Program
from app.modules.crm.models.lead import CrmLead
from app.modules.lms.models import StudentProfile

router = APIRouter(prefix="/api/v1/public/campus", tags=["public-campus"])


class CampusSummary(BaseModel):
    total_programs: int = Field(ge=0)
    total_leads: int = Field(ge=0)
    followups_today: int = Field(ge=0)
    total_students: int = Field(ge=0)
    generated_at: datetime


@router.get("/summary", response_model=CampusSummary)
async def get_campus_summary(
    response: Response, db: AsyncSession = Depends(get_db)
) -> CampusSummary:
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    start = datetime.combine(now.date(), time.min)
    end = start + timedelta(days=1)
    totals = (await db.execute(select(
        select(func.count()).select_from(Program).scalar_subquery(),
        select(func.count()).select_from(CrmLead)
        .where(CrmLead.is_archived.is_(False)).scalar_subquery(),
        select(func.count()).select_from(CrmLead)
        .where(
            CrmLead.is_archived.is_(False),
            CrmLead.stage != "enrolled",
            CrmLead.followup_date >= start,
            CrmLead.followup_date < end,
        ).scalar_subquery(),
        select(func.count()).select_from(StudentProfile)
        .join(User, User.user_id == StudentProfile.user_id)
        .scalar_subquery(),
    ))).one()
    return CampusSummary(
        total_programs=totals[0],
        total_leads=totals[1],
        followups_today=totals[2],
        total_students=totals[3],
        generated_at=datetime.now(timezone.utc),
    )
