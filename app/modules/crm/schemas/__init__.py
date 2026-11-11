from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, model_validator


LEAD_STATUSES = (
    "new_lead", "uncontactable", "contacted", "follow_up", "potential", "future_prospect",
    "not_interested", "enrolled", "lost_to_competitor", "cant_afford",
)
CallOutcome = Literal["connected", "no_answer", "busy", "wrong_number", "callback_requested"]
CALL_OUTCOME_LABELS = {
    "connected": "Connected",
    "no_answer": "No answer",
    "busy": "Busy",
    "wrong_number": "Wrong number",
    "callback_requested": "Callback requested",
}
# A call with one of these outcomes never reached the person.
UNREACHED_CALL_OUTCOMES = ("no_answer", "busy", "wrong_number")
LOSS_REASONS = (
    "Fees Beyond Expectations", "Programme Not Available", "Joined Another Institute",
    "Entry Requirements Issue", "Prefers Physical Classes", "Prefers Another Study Mode",
    "Programme Duration Not Suitable", "Schedule Not Suitable",
    "Qualification / Recognition Concern", "Parent or Guardian Not Interested",
    "Decided to Postpone Studies", "No Longer Interested in Higher Education", "Other",
)
POTENTIAL_REASONS = (
    "Payment Details Sent — Awaiting Payment",
    "Plans to Pay on an Agreed Date",
    "Awaiting Salary / Funds",
    "Awaiting Parent / Sponsor Approval",
    "Still Deciding / Interested in a Later Intake",
)
AFFORDABILITY_REASONS = (
    "Programme Fee Too High", "Initial Payment Too High", "Monthly Installment Too High",
    "Registration Fee Issue", "Currently No Financial Capacity", "Waiting for Salary / Income",
    "Waiting for Parent / Sponsor Support", "Looking for Scholarship / Discount",
    "Competitor Offering Lower Price", "Other",
)
DELAY_REASONS = (
    "Waiting for Exam Results", "Financial Planning", "Currently Studying Another Programme",
    "Planning for a Future Intake", "Waiting for Employment", "Need Parent / Sponsor Approval",
    "Currently Overseas", "Other",
)


def validate_status_fields(values):
    if values.stage not in LEAD_STATUSES:
        raise ValueError("Unsupported lead status")
    if values.stage in ("not_interested", "lost_to_competitor"):
        if values.status_reason not in LOSS_REASONS:
            raise ValueError("Select a reason for this outcome")
        if values.status_reason == "Other" and not values.status_remarks:
            raise ValueError("Enter remarks for Other")
    if values.stage == "cant_afford" and values.affordability_reason not in AFFORDABILITY_REASONS:
        raise ValueError("Select an affordability reason")
    if values.stage == "future_prospect" and values.delay_reason not in DELAY_REASONS:
        raise ValueError("Select a delay reason")
    if values.stage == "cant_afford" and values.affordability_reason == "Other" and not values.status_remarks:
        raise ValueError("Enter remarks for Other")
    if values.stage == "future_prospect" and values.delay_reason == "Other" and not values.status_remarks:
        raise ValueError("Enter remarks for Other")
    if values.stage == "enrolled":
        if not values.email and not getattr(values, "legacy_enrolled_email_pending", False):
            raise ValueError("Email is required for enrollment")
        if values.email:
            TypeAdapter(EmailStr).validate_python(values.email)
    return values


class CrmActivityOut(BaseModel):
    activity_id: int
    lead_id: int
    activity_type: str
    content: str
    call_outcome: Optional[str] = None
    counsellor_name: Optional[str] = None
    counsellor_id: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CrmActivityCreate(BaseModel):
    activity_type: str
    content: str = ""
    call_outcome: Optional[CallOutcome] = None

    @model_validator(mode="after")
    def _check_call_outcome(self):
        if self.activity_type == "call":
            if self.call_outcome is None:
                raise ValueError("Select a call outcome")
        elif self.call_outcome is not None:
            raise ValueError("Only a call can have a call outcome")
        elif not self.content.strip():
            raise ValueError("Enter the activity details")
        return self


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
    meta_counsellor_ids: list[int] = []
    meta_setting_ready: bool = False


class CrmAssignmentSettingsUpdate(BaseModel):
    auto_assign_enabled: Optional[bool] = None
    meta_counsellor_ids: Optional[list[int]] = Field(None, max_length=50)


class CrmCounsellorOrderUpdate(BaseModel):
    user_ids: list[int]


class CrmLeadOut(BaseModel):
    score: Optional[int] = None
    priority_locked: bool = False
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
    contact_visitor_ip: Optional[str] = None
    contact_peer_ip: Optional[str] = None
    source: str
    stage: str
    status_reason: Optional[str] = None
    status_remarks: Optional[str] = None
    affordability_reason: Optional[str] = None
    expected_intake: Optional[str] = None
    expected_month: Optional[str] = None
    delay_reason: Optional[str] = None
    campaign: Optional[str] = None
    academic_course_id: Optional[int] = None
    programme_fee: Optional[float] = None
    registration_fee: Optional[float] = None
    programme_duration: Optional[str] = None
    intake: Optional[str] = None
    payment_plan: Optional[str] = None
    last_contacted_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    followup_count: int = 0
    enrolled_at: Optional[datetime] = None
    student_user_id: Optional[int] = None
    student_id: Optional[str] = None
    payment_status: Optional[str] = None
    amount_paid: Optional[float] = None
    payment_date: Optional[datetime] = None
    enrollment_email_status: Optional[str] = None
    enrollment_email_error: Optional[str] = None
    enrollment_email_sent_at: Optional[datetime] = None
    priority: str
    assigned_counsellor_id: Optional[int] = None
    assigned_at: Optional[datetime] = None
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
    external_record_id: Optional[str] = None
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    interested_programme: Optional[str] = None
    interested_course: Optional[str] = None
    awarding_body: Optional[str] = None
    source: str
    stage: str
    campaign: Optional[str] = None
    academic_course_id: Optional[int] = None
    last_contacted_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    followup_count: int = 0
    priority: str
    amount: Optional[float] = None
    counsellor_name: Optional[str] = None
    assigned_counsellor_id: Optional[int] = None
    assigned_at: Optional[datetime] = None
    followup_date: Optional[datetime] = None
    followup_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    score: Optional[int] = None
    win_probability: Optional[float] = None

    model_config = {"from_attributes": True}


class CrmPriorityLead(CrmLeadSummary):
    next_action: str = ""


class CrmPriorityQueueResponse(BaseModel):
    leads: list[CrmPriorityLead]
    open_leads: int = 0
    expected_enrolments: float = 0


class CrmLeadCreate(BaseModel):
    full_name: str
    created_at: Optional[datetime] = None
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
    stage: str = "new_lead"
    status_reason: Optional[str] = None
    status_remarks: Optional[str] = None
    affordability_reason: Optional[str] = None
    expected_intake: Optional[str] = None
    expected_month: Optional[str] = None
    delay_reason: Optional[str] = None
    campaign: Optional[str] = None
    academic_course_id: Optional[int] = None
    programme_fee: Optional[float] = None
    registration_fee: Optional[float] = None
    programme_duration: Optional[str] = None
    intake: Optional[str] = None
    payment_plan: Optional[str] = None
    followup_date: Optional[datetime] = None
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
    referral_code: Optional[str] = None
    assigned_counsellor_id: Optional[int] = None


class CrmLeadUpdate(BaseModel):
    full_name: Optional[str] = None
    created_at: Optional[datetime] = None
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
    status_reason: Optional[str] = None
    status_remarks: Optional[str] = None
    affordability_reason: Optional[str] = None
    expected_intake: Optional[str] = None
    expected_month: Optional[str] = None
    delay_reason: Optional[str] = None
    campaign: Optional[str] = None
    academic_course_id: Optional[int] = None
    programme_fee: Optional[float] = None
    registration_fee: Optional[float] = None
    programme_duration: Optional[str] = None
    intake: Optional[str] = None
    payment_plan: Optional[str] = None
    priority: Optional[str] = None
    priority_locked: Optional[bool] = None
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
    status_reason: Optional[str] = None
    status_remarks: Optional[str] = None
    affordability_reason: Optional[str] = None
    expected_intake: Optional[str] = None
    expected_month: Optional[str] = None
    delay_reason: Optional[str] = None
    followup_date: Optional[datetime] = None


class CrmFollowupComplete(BaseModel):
    note: str = Field(min_length=1)
    outcome: Literal["contacted", "no_answer", "requested_callback"]
    next_followup_date: Optional[datetime] = None


class CrmLeadListResponse(BaseModel):
    data: list[CrmLeadSummary]
    total: int


class CrmLeadFilterOptions(BaseModel):
    awarding_bodies: list[str] = []
    programmes: list[str] = []
    sources: list[str] = []
    cities: list[str] = []
    countries: list[str] = []
    campaigns: list[str] = []
    intakes: list[str] = []


class CrmPipelineStage(BaseModel):
    stage: str
    count: int
    leads: list[CrmLeadSummary]


class CrmPipelineResponse(BaseModel):
    stages: list[CrmPipelineStage]


class CrmDashboardStats(BaseModel):
    total_leads: int = 0
    today_leads: int = 0
    recently_active_leads: int = 0
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
    leads_contacted: int = 0
    leads_not_contacted: int = 0
    followups_completed: int = 0
    followups_overdue: int = 0
    future_prospects: int = 0
    follow_up: int = 0
    potential: int = 0
    uncontactable: int = 0
    not_interested: int = 0
    lost_to_competitor: int = 0
    cant_afford: int = 0
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
    by_source_enrolled: dict[str, int] = {}
    by_programme: dict[str, int] = {}
    by_programme_enrolled: dict[str, int] = {}
    by_awarding_body: dict[str, int] = {}
    daily: list[CrmReportDailyPoint] = []


# ---------------------------------------------------------------------------
# WhatsApp bot (insbot) sync payloads
# ---------------------------------------------------------------------------
class WhatsAppBotCounsellor(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


class WhatsAppBotTranscriptMessage(BaseModel):
    sender: str = Field(..., max_length=20)
    message: str = Field(..., max_length=10000)
    sent_at: Optional[datetime] = None


class WhatsAppBotLeadSync(BaseModel):
    bot_lead_id: str = Field(..., min_length=1, max_length=80)
    bot_session_id: Optional[str] = Field(None, max_length=100)
    phone: str = Field(..., min_length=5, max_length=50)
    full_name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    qualification: Optional[str] = Field(None, max_length=100)
    pathway: Optional[str] = Field(None, max_length=100)
    field: Optional[str] = Field(None, max_length=100)
    course_interest: Optional[str] = Field(None, max_length=255)
    bot_status: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = Field(None, max_length=5000)
    first_contact_at: Optional[datetime] = None
    handover_reason: Optional[str] = Field(None, max_length=500)
    handed_over_at: Optional[datetime] = None
    counsellor: Optional[WhatsAppBotCounsellor] = None
    transcript: list[WhatsAppBotTranscriptMessage] = Field(default_factory=list, max_length=1000)


class WhatsAppBotMessageSync(BaseModel):
    bot_lead_id: Optional[str] = Field(None, max_length=80)
    phone: str = Field(..., min_length=5, max_length=50)
    sender: str = Field(..., max_length=20)
    message: str = Field(..., min_length=1, max_length=10000)
    sent_at: Optional[datetime] = None
    counsellor_name: Optional[str] = Field(None, max_length=255)
