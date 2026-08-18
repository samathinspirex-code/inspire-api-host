from typing import Literal

from pydantic import BaseModel, EmailStr, Field

AccessKey = Literal[
    "USER_MANAGEMENT",
    "CMS",
    "LMS",
    "SUPER_ADMIN",
    "ADMIN",
    "LECTURER",
    "STUDENT",
]


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    access: list[AccessKey] = []


class UserUpdate(UserCreate):
    pass


class UserDetail(BaseModel):
    user_id: int
    name: str
    email: str
    access: list[str]
    authenticator_configured: bool = False


class UserListResponse(BaseModel):
    data: list[UserDetail]
