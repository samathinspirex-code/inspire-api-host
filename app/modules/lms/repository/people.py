from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import AccessLevel, User, UserAccessLevel
from app.modules.lms.models import LecturerProfile, StudentProfile


class PeopleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_students(self, search: str | None) -> list[tuple[User, StudentProfile]]:
        stmt = select(User, StudentProfile).join(StudentProfile, StudentProfile.user_id == User.user_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                    StudentProfile.student_number.ilike(pattern),
                )
            )
        return [(row[0], row[1]) for row in (await self.db.execute(stmt.order_by(User.full_name))).all()]

    async def list_lecturers(self, search: str | None) -> list[tuple[User, LecturerProfile]]:
        stmt = select(User, LecturerProfile).join(LecturerProfile, LecturerProfile.user_id == User.user_id)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                    LecturerProfile.staff_number.ilike(pattern),
                    LecturerProfile.job_title.ilike(pattern),
                )
            )
        return [(row[0], row[1]) for row in (await self.db.execute(stmt.order_by(User.full_name))).all()]

    async def get_user(self, user_id: int) -> User | None:
        stmt = (
            select(User)
            .where(User.user_id == user_id)
            .options(selectinload(User.access_levels).selectinload(UserAccessLevel.access_level))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_user_by_email(self, email: str, exclude_user_id: int | None = None) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.lower())
        if exclude_user_id is not None:
            stmt = stmt.where(User.user_id != exclude_user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_student_profile(self, user_id: int) -> StudentProfile | None:
        return await self.db.get(StudentProfile, user_id)

    async def get_lecturer_profile(self, user_id: int) -> LecturerProfile | None:
        return await self.db.get(LecturerProfile, user_id)

    async def student_number_exists(self, number: str, exclude_user_id: int | None = None) -> bool:
        stmt = select(StudentProfile.user_id).where(func.lower(StudentProfile.student_number) == number.lower())
        if exclude_user_id is not None:
            stmt = stmt.where(StudentProfile.user_id != exclude_user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def staff_number_exists(self, number: str, exclude_user_id: int | None = None) -> bool:
        stmt = select(LecturerProfile.user_id).where(func.lower(LecturerProfile.staff_number) == number.lower())
        if exclude_user_id is not None:
            stmt = stmt.where(LecturerProfile.user_id != exclude_user_id)
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def access_levels(self, keys: list[str]) -> list[AccessLevel]:
        stmt = select(AccessLevel).where(AccessLevel.access_key.in_(keys), AccessLevel.is_active.is_(True))
        return list((await self.db.execute(stmt)).scalars().all())

    async def create_student(self, user_data: dict[str, Any], profile_data: dict[str, Any], access: list[AccessLevel]) -> tuple[User, StudentProfile]:
        user = User(**user_data)
        user.access_levels = [UserAccessLevel(access_level=item, assigned_by=user_data.get("created_by")) for item in access]
        self.db.add(user)
        await self.db.flush()
        profile = StudentProfile(user_id=user.user_id, **profile_data)
        self.db.add(profile)
        await self.db.commit()
        await self.db.refresh(user)
        await self.db.refresh(profile)
        return user, profile

    async def create_lecturer(self, user_data: dict[str, Any], profile_data: dict[str, Any], access: list[AccessLevel]) -> tuple[User, LecturerProfile]:
        user = User(**user_data)
        user.access_levels = [UserAccessLevel(access_level=item, assigned_by=user_data.get("created_by")) for item in access]
        self.db.add(user)
        await self.db.flush()
        profile = LecturerProfile(user_id=user.user_id, **profile_data)
        self.db.add(profile)
        await self.db.commit()
        await self.db.refresh(user)
        await self.db.refresh(profile)
        return user, profile

    async def update_person(self, user: User, profile, user_data: dict[str, Any], profile_data: dict[str, Any]):
        for field, value in user_data.items():
            setattr(user, field, value)
        for field, value in profile_data.items():
            setattr(profile, field, value)
        await self.db.commit()
        await self.db.refresh(user)
        await self.db.refresh(profile)
        return user, profile

    async def set_active(self, user: User, is_active: bool) -> User:
        user.is_active = is_active
        await self.db.commit()
        await self.db.refresh(user)
        return user
