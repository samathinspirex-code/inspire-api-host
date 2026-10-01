from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CrmActivityOut(BaseModel):
    activity_id: int
    lead_id: int
    activity_type: str
    content: str
    counsellor_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CrmActivityCreate(BaseModel):
    activity_type: str
    content: str


class CrmCounsellorOut(BaseModel):
    user_id: int
    name: str
    email: str
    is_active: bool = True


class CrmCounsellorLeadPreview(BaseModel):
    lead_id: int
    full_name: str
    phone: Optional[str] = None
    stage: str
    interested_course: Optional[str] = None
    interested_programme: Optional[str] = None


class CrmCounsellorRosterItem(BaseModel):
    user_id: int
    name: str
    email: str
    is_active: bool
    assign_order: int = 0
    assigned_leads: int
    active_leads: int
    leads: list[CrmCounsellorLeadPreview] = []


class CrmCounsellorStatusUpdate(BaseModel):
    is_active: bool


class CrmAssignmentSettingsOut(BaseModel):
    auto_assign_enabled: bool
    last_counsellor_id: Optional[int] = None
    last_assigned_on: Optional[str] = None
    next_counsellor_id: Optional[int] = None
    next_counsellor_name: Optional[str] = None
    rotation: list[CrmCounsellorOut] = []


class CrmAssignmentSettingsUpdate(BaseModel):
    auto_assign_enabled: bool


class CrmCounsellorOrderUpdate(BaseModel):
    user_ids: list[int]


class CrmLeadOut(BaseModel):
    lead_id: int
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    highest_qualification: Optional[str] = None
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    awarding_body: Optional[str] = None
    message: Optional[str] = None
    source: str
    stage: str
    priority: str
    assigned_counsellor_id: Optional[int] = None
    counsellor_name: Optional[str] = None
    notes: Optional[str] = None
    followup_date: Optional[datetime] = None
    followup_type: Optional[str] = None
    is_archived: bool
    amount: Optional[float] = None
    school: Optional[str] = None
    nationality: Optional[str] = None
    faculty: Optional[str] = None
    student_status: Optional[str] = None
    parents_occupation: Optional[str] = None
    parents_email: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    country: Optional[str] = None
    social_lead_id: Optional[str] = None
    external_record_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    activities: list[CrmActivityOut] = []

    model_config = {"from_attributes": True}


class CrmLeadSummary(BaseModel):
    """Lightweight version for list views / kanban."""

    lead_id: int
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    awarding_body: Optional[str] = None
    source: str
    stage: str
    priority: str
    amount: Optional[float] = None
    counsellor_name: Optional[str] = None
    assigned_counsellor_id: Optional[int] = None
    followup_date: Optional[datetime] = None
    followup_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CrmLeadCreate(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    highest_qualification: Optional[str] = None
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    awarding_body: Optional[str] = None
    message: Optional[str] = None
    source: str = "manual"
    stage: str = "new_inquiry"
    priority: str = "medium"
    notes: Optional[str] = None
    amount: Optional[float] = None
    school: Optional[str] = None
    nationality: Optional[str] = None
    faculty: Optional[str] = None
    student_status: Optional[str] = None
    parents_occupation: Optional[str] = None
    parents_email: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    country: Optional[str] = None
    social_lead_id: Optional[str] = None
    external_record_id: Optional[str] = None
    assigned_counsellor_id: Optional[int] = None


class CrmLeadUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    highest_qualification: Optional[str] = None
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    awarding_body: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = None
    stage: Optional[str] = None
    priority: Optional[str] = None
    assigned_counsellor_id: Optional[int] = None
    counsellor_name: Optional[str] = None
    notes: Optional[str] = None
    followup_date: Optional[datetime] = None
    followup_type: Optional[str] = None
    is_archived: Optional[bool] = None
    amount: Optional[float] = None
    school: Optional[str] = None
    nationality: Optional[str] = None
    faculty: Optional[str] = None
    student_status: Optional[str] = None
    parents_occupation: Optional[str] = None
    parents_email: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    country: Optional[str] = None
    social_lead_id: Optional[str] = None
    external_record_id: Optional[str] = None


class CrmStageUpdate(BaseModel):
    """Used for drag-drop kanban stage changes."""

    stage: str


class CrmLeadListResponse(BaseModel):
    data: list[CrmLeadSummary]
    total: int


class CrmLeadFilterOptions(BaseModel):
    awarding_bodies: list[str] = []
    programmes: list[str] = []


class CrmPipelineStage(BaseModel):
    stage: str
    count: int
    leads: list[CrmLeadSummary]


class CrmPipelineResponse(BaseModel):
    stages: list[CrmPipelineStage]


class CrmDashboardStats(BaseModel):
    total_leads: int = 0
    new_leads_30d: int
    awaiting_contact: int
    in_progress: int
    offers_sent: int
    enrolled_30d: int
    conversion_rate: float
    followups_today: int
    unassigned: int = 0
    by_source: dict[str, int]
    by_programme: dict[str, int] = {}
    pipeline_counts: dict[str, int]


class CrmDashboardResponse(BaseModel):
    stats: CrmDashboardStats
    recent_leads: list[CrmLeadSummary]
    followups_today: list[CrmLeadSummary]


class CrmCounsellorReportRow(BaseModel):
    counsellor_id: Optional[int] = None
    counsellor_name: str
    new_leads: int = 0
    activities: int = 0
    offers: int = 0
    enrolled: int = 0
    lost: int = 0
    followups: int = 0
    current_assigned: int = 0
    conversion_rate: float = 0
    by_stage: dict[str, int] = {}


class CrmReportDailyPoint(BaseModel):
    date: str
    new_leads: int = 0
    enrolled: int = 0
    activities: int = 0


class CrmCounsellorReportResponse(BaseModel):
    date_from: str
    date_to: str
    counsellor_id: Optional[int] = None
    totals: CrmCounsellorReportRow
    counsellors: list[CrmCounsellorReportRow] = []
    by_stage: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_awarding_body: dict[str, int] = {}
    daily: list[CrmReportDailyPoint] = []
