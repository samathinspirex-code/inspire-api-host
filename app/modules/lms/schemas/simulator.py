from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SimKpi(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{1,24}$")
    label: str = Field(min_length=1, max_length=60)
    start: float
    direction: Literal[1, -1] = 1
    scale: float = Field(gt=0)
    prefix: str = Field(default="", max_length=12)
    suffix: str = Field(default="", max_length=12)
    decimals: int = Field(default=0, ge=0, le=3)


class SimOption(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    effects: dict[str, float] = Field(default_factory=dict)


class SimDecision(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    options: list[SimOption] = Field(min_length=2, max_length=4)


class SimRound(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(min_length=1, max_length=2000)
    event: str | None = Field(default=None, max_length=500)
    decisions: list[SimDecision] = Field(min_length=1, max_length=4)


class SimContent(BaseModel):
    kpis: list[SimKpi] = Field(min_length=1, max_length=4)
    rounds: list[SimRound] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_effects(self):
        keys = [kpi.key for kpi in self.kpis]
        if len(set(keys)) != len(keys):
            raise ValueError("Each KPI needs its own key")
        for round_ in self.rounds:
            for decision in round_.decisions:
                for option in decision.options:
                    unknown = set(option.effects) - set(keys)
                    if unknown:
                        raise ValueError(f"Option \"{option.text}\" changes an unknown KPI: {', '.join(sorted(unknown))}")
        return self


class SimRole(BaseModel):
    key: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    responsibility: str = Field(default="", max_length=300)


def _check_roles(roles: list[SimRole]) -> None:
    names = [role.name.strip().lower() for role in roles]
    if len(set(names)) != len(names):
        raise ValueError("Each role needs its own name")


class SimTemplateWrite(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    tagline: str = Field(default="", max_length=400)
    programme: str = Field(default="", max_length=120)
    level: Literal["ug", "pg", "cpd"] = "ug"
    tone: Literal["student", "professional"] = "student"
    content: SimContent
    roles: list[SimRole] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_roles(self):
        _check_roles(self.roles)
        return self


class SimTemplateUpdate(SimTemplateWrite):
    pass


class SimTemplateArchive(BaseModel):
    is_archived: bool


class _RunSettings(BaseModel):
    roles: list[SimRole] = Field(min_length=1, max_length=8)
    min_team_size: int = Field(default=2, ge=1, le=8)
    max_team_size: int = Field(default=4, ge=1, le=8)
    allow_solo: bool = False
    opens_at: datetime
    closes_at: datetime
    xp_per_round: int = Field(default=100, ge=0, le=1000)
    points_per_round: int = Field(default=30, ge=0, le=1000)

    @model_validator(mode="after")
    def check_settings(self):
        _check_roles(self.roles)
        if self.closes_at <= self.opens_at:
            raise ValueError("The close date must be after the open date")
        if self.min_team_size > self.max_team_size:
            raise ValueError("Minimum team size cannot be more than the maximum")
        if self.max_team_size > len(self.roles):
            raise ValueError("Add at least as many roles as the maximum team size")
        return self


class SimRunCreate(_RunSettings):
    template_id: int


class SimRunUpdate(_RunSettings):
    status: Literal["open", "closed"] = "open"


class SimXpAdjustment(BaseModel):
    student_user_id: int | None = None
    team_id: int | None = None
    xp: int = Field(default=0, ge=-1000, le=1000)
    points: int = Field(default=0, ge=-1000, le=1000)
    reason: str = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def check_target(self):
        if (self.student_user_id is None) == (self.team_id is None):
            raise ValueError("Choose one student or one team")
        if self.xp == 0 and self.points == 0:
            raise ValueError("Enter some XP or points")
        return self


class SimTeamCreate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    solo: bool = False
    role_key: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def check_name(self):
        if not self.solo and len((self.name or "").strip()) < 2:
            raise ValueError("Enter a team name")
        return self


class SimInviteCreate(BaseModel):
    student_user_id: int


class SimInviteRespond(BaseModel):
    accept: bool


class SimRoleChoice(BaseModel):
    role_key: str = Field(min_length=1, max_length=40)


class SimVote(BaseModel):
    decision_index: int = Field(ge=0)
    option_index: int = Field(ge=0)


class SimMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
