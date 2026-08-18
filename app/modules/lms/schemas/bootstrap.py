from pydantic import BaseModel


class NavigationItem(BaseModel):
    key: str
    label: str
    icon: str


class DashboardMetric(BaseModel):
    label: str
    value: str
    hint: str


class LmsBootstrapResponse(BaseModel):
    role: str
    role_label: str
    navigation: list[NavigationItem]
    metrics: list[DashboardMetric]
    enabled_features: list[str]
