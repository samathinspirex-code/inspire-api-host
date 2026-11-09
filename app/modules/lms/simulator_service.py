"""Team simulators: templates, class runs, teams, invites, votes, chat and XP."""
from collections import defaultdict
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.modules.auth.models import User
from app.modules.lms import notification_service, simulator_engine as engine
from app.modules.lms.models import (
    ClassLecturer, ClassStudent, CourseEnrollment, LmsClass, LmsSimInvite, LmsSimMessage, LmsSimRoundResult,
    LmsSimRun, LmsSimTeam, LmsSimTeamMember, LmsSimTemplate, LmsSimVote, LmsXpLedger,
)
from app.modules.lms.rubric_service import is_global_admin
from app.modules.lms.schemas.simulator import SimRole

MESSAGE_LIMIT_PER_MINUTE = 20
_now = notification_service.utc_now


# --------------------------------------------------------------------------- shared helpers

async def _names(db: AsyncSession, user_ids) -> dict[int, str]:
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    rows = (await db.execute(select(User.user_id, User.full_name).where(User.user_id.in_(ids)))).all()
    return {user_id: (name or "").strip() or f"Student {user_id}" for user_id, name in rows}


def _normalise_roles(roles: list[SimRole]) -> list[dict]:
    used: set[str] = {role.key for role in roles if role.key}
    result, counter = [], 1
    for role in roles:
        key = role.key
        while not key:
            candidate = f"role_{counter}"
            counter += 1
            if candidate not in used:
                key = candidate
                used.add(candidate)
        result.append({"key": key, "name": role.name.strip(), "responsibility": role.responsibility.strip()})
    return result


def _role_name(run: LmsSimRun, role_key: str | None) -> str | None:
    return next((role["name"] for role in run.roles if role["key"] == role_key), None)


def _window(run: LmsSimRun, now=None) -> str:
    now = now or _now()
    if run.status == "closed" or now >= run.closes_at:
        return "closed"
    return "upcoming" if now < run.opens_at else "open"


def _ensure_open(run: LmsSimRun) -> None:
    state = _window(run)
    if state == "upcoming":
        raise ValidationError("This simulator has not opened yet")
    if state == "closed":
        raise ValidationError("This simulator has closed")


def _team_status(team: LmsSimTeam, run: LmsSimRun) -> str:
    return "incomplete" if team.status != "completed" and _window(run) == "closed" else team.status


def _team_score(team: LmsSimTeam, run: LmsSimRun) -> int:
    return team.final_score if team.final_score is not None else engine.score(run.content, team.kpi_state["values"])


async def _ensure_class_manager(db: AsyncSession, class_id: int, user_id: int) -> LmsClass:
    class_ = await db.get(LmsClass, class_id)
    if class_ is None:
        raise NotFoundError("Class not found")
    if await db.get(ClassLecturer, (class_id, user_id)) is None and not await is_global_admin(db, user_id):
        raise ForbiddenError("You can manage simulators only for classes assigned to you")
    return class_


async def _ensure_class_student(db: AsyncSession, class_id: int, user_id: int) -> LmsClass:
    class_ = await db.get(LmsClass, class_id)
    if class_ is None or class_.status == "cancelled":
        raise NotFoundError("Class not found")
    if await db.get(ClassStudent, (class_id, user_id)) is None:
        raise ForbiddenError("You are not in this class")
    enrollment = await db.get(CourseEnrollment, (class_.course_id, user_id))
    if enrollment is None or enrollment.status != "enrolled":
        raise ForbiddenError("You are not enrolled in this course")
    return class_


async def _get_run(db: AsyncSession, run_id: int) -> LmsSimRun:
    run = await db.get(LmsSimRun, run_id)
    if run is None:
        raise NotFoundError("Simulator not found")
    return run


async def _class_student_ids(db: AsyncSession, class_id: int) -> list[int]:
    class_ = await db.get(LmsClass, class_id)
    if class_ is None:
        return []
    rows = await db.execute(
        select(ClassStudent.student_user_id)
        .join(CourseEnrollment, (CourseEnrollment.student_user_id == ClassStudent.student_user_id) & (CourseEnrollment.course_id == class_.course_id))
        .where(ClassStudent.class_id == class_id, CourseEnrollment.status == "enrolled")
    )
    return list(rows.scalars().all())


async def _members(db: AsyncSession, team_id: int) -> list[LmsSimTeamMember]:
    rows = await db.execute(select(LmsSimTeamMember).where(LmsSimTeamMember.team_id == team_id).order_by(LmsSimTeamMember.joined_at))
    return list(rows.scalars().all())


async def _active_member_ids(db: AsyncSession, team: LmsSimTeam, run: LmsSimRun) -> list[int]:
    """Members still in the class. Withdrawn students stop counting as voters."""
    enrolled = set(await _class_student_ids(db, run.class_id))
    return [member.student_user_id for member in await _members(db, team.team_id) if member.student_user_id in enrolled]


async def _ballots(db: AsyncSession, team_id: int, round_index: int, member_ids) -> list[engine.Ballot]:
    rows = (await db.execute(select(LmsSimVote).where(LmsSimVote.team_id == team_id, LmsSimVote.round_index == round_index))).scalars().all()
    allowed = set(member_ids)
    return [engine.Ballot(vote.student_user_id, vote.decision_index, vote.option_index, vote.voted_at) for vote in rows if vote.student_user_id in allowed]


async def _award(db: AsyncSession, *, student_user_id: int, run: LmsSimRun, team_id: int | None, source_type: str, xp: int, points: int, reason: str, event_key: str, awarded_by: int | None = None) -> None:
    await db.execute(insert(LmsXpLedger).values(
        student_user_id=student_user_id, class_id=run.class_id, source_type=source_type, run_id=run.run_id, team_id=team_id,
        xp=xp, points=points, reason=reason[:300], awarded_by=awarded_by, event_key=event_key,
    ).on_conflict_do_nothing(index_elements=["event_key"]))


# --------------------------------------------------------------------------- templates

def _template_item(template: LmsSimTemplate, user_id: int, admin: bool, include_content: bool = False) -> dict:
    item = {
        "template_id": template.template_id, "title": template.title, "tagline": template.tagline,
        "programme": template.programme, "level": template.level, "tone": template.tone,
        "is_starter": template.starter_key is not None, "is_archived": template.is_archived,
        "can_edit": template.created_by == user_id or (admin and template.starter_key is None),
        "can_archive": template.created_by == user_id or admin,
        "round_count": len(template.content["rounds"]), "kpi_count": len(template.content["kpis"]),
        "roles": template.roles, "updated_at": template.updated_at,
    }
    if include_content:
        item["content"] = template.content
    return item


async def list_templates(db: AsyncSession, user_id: int, include_archived: bool = False) -> dict:
    admin = await is_global_admin(db, user_id)
    query = select(LmsSimTemplate)
    if not admin:
        query = query.where(or_(LmsSimTemplate.starter_key.is_not(None), LmsSimTemplate.created_by == user_id))
    if not include_archived:
        query = query.where(LmsSimTemplate.is_archived.is_(False))
    rows = (await db.execute(query.order_by(LmsSimTemplate.starter_key.is_(None).desc(), LmsSimTemplate.updated_at.desc()))).scalars().all()
    return {"data": [_template_item(template, user_id, admin) for template in rows]}


async def _readable_template(db: AsyncSession, template_id: int, user_id: int) -> tuple[LmsSimTemplate, bool]:
    template = await db.get(LmsSimTemplate, template_id)
    if template is None:
        raise NotFoundError("Simulator template not found")
    admin = await is_global_admin(db, user_id)
    if template.starter_key is None and template.created_by != user_id and not admin:
        raise ForbiddenError("This simulator template belongs to another lecturer")
    return template, admin


async def get_template(db: AsyncSession, template_id: int, user_id: int) -> dict:
    template, admin = await _readable_template(db, template_id, user_id)
    return _template_item(template, user_id, admin, True)


async def create_template(db: AsyncSession, payload, user_id: int) -> dict:
    template = LmsSimTemplate(
        title=payload.title.strip(), tagline=payload.tagline.strip(), programme=payload.programme.strip(), level=payload.level,
        tone=payload.tone, content=payload.content.model_dump(), roles=_normalise_roles(payload.roles), created_by=user_id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_item(template, user_id, await is_global_admin(db, user_id), True)


async def update_template(db: AsyncSession, template_id: int, payload, user_id: int) -> dict:
    template, admin = await _readable_template(db, template_id, user_id)
    if template.starter_key is not None:
        raise ForbiddenError("Starter templates can't be changed. Copy it first, then edit your copy")
    if template.created_by != user_id and not admin:
        raise ForbiddenError("Only the lecturer who created this template or an admin can change it")
    template.title, template.tagline, template.programme = payload.title.strip(), payload.tagline.strip(), payload.programme.strip()
    template.level, template.tone = payload.level, payload.tone
    template.content, template.roles = payload.content.model_dump(), _normalise_roles(payload.roles)
    await db.commit()
    await db.refresh(template)
    return _template_item(template, user_id, admin, True)


async def copy_template(db: AsyncSession, template_id: int, user_id: int) -> dict:
    source, admin = await _readable_template(db, template_id, user_id)
    template = LmsSimTemplate(
        source_template_id=source.template_id, title=f"{source.title} (copy)"[:160], tagline=source.tagline, programme=source.programme,
        level=source.level, tone=source.tone, content=source.content, roles=source.roles, created_by=user_id,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return _template_item(template, user_id, admin, True)


async def archive_template(db: AsyncSession, template_id: int, is_archived: bool, user_id: int) -> dict:
    template, admin = await _readable_template(db, template_id, user_id)
    if template.created_by != user_id and not admin:
        raise ForbiddenError("Only the lecturer who created this template or an admin can archive it")
    template.is_archived = is_archived
    await db.commit()
    await db.refresh(template)
    return _template_item(template, user_id, admin)


# --------------------------------------------------------------------------- lecturer: runs and results

def _run_item(run: LmsSimRun) -> dict:
    return {
        "run_id": run.run_id, "class_id": run.class_id, "template_id": run.template_id, "title": run.title, "tagline": run.tagline,
        "tone": run.tone, "roles": run.roles, "min_team_size": run.min_team_size, "max_team_size": run.max_team_size,
        "allow_solo": run.allow_solo, "opens_at": run.opens_at, "closes_at": run.closes_at, "xp_per_round": run.xp_per_round,
        "points_per_round": run.points_per_round, "status": run.status, "window": _window(run), "total_rounds": len(run.content["rounds"]),
    }


async def apply_template(db: AsyncSession, class_id: int, payload, user_id: int) -> dict:
    await _ensure_class_manager(db, class_id, user_id)
    template, _ = await _readable_template(db, payload.template_id, user_id)
    if template.is_archived:
        raise ValidationError("This template is archived")
    run = LmsSimRun(
        class_id=class_id, template_id=template.template_id, title=template.title, tagline=template.tagline, tone=template.tone,
        content=template.content, roles=_normalise_roles(payload.roles), min_team_size=payload.min_team_size, max_team_size=payload.max_team_size,
        allow_solo=payload.allow_solo, opens_at=payload.opens_at, closes_at=payload.closes_at, xp_per_round=payload.xp_per_round,
        points_per_round=payload.points_per_round, created_by=user_id,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    students = await _class_student_ids(db, class_id)
    if students:
        await notification_service.notify_simulator(db, students, f"sim:{run.run_id}:applied", f"New simulator: {run.title}", "A new simulator is ready for your class.", f"class={class_id}&run={run.run_id}")
        await db.commit()
    return _run_item(run)


async def list_class_runs(db: AsyncSession, class_id: int, user_id: int) -> dict:
    await _ensure_class_manager(db, class_id, user_id)
    runs = (await db.execute(select(LmsSimRun).where(LmsSimRun.class_id == class_id).order_by(LmsSimRun.opens_at.desc()))).scalars().all()
    stats = {row.run_id: row for row in (await db.execute(
        select(
            LmsSimTeam.run_id, func.count().label("teams"),
            func.sum(case((LmsSimTeam.status == "completed", 1), else_=0)).label("completed"),
            func.avg(LmsSimTeam.final_score).label("average"),
        ).where(LmsSimTeam.run_id.in_([run.run_id for run in runs] or [0])).group_by(LmsSimTeam.run_id)
    )).all()}
    data = []
    for run in runs:
        row = stats.get(run.run_id)
        data.append({**_run_item(run), "team_count": row.teams if row else 0, "completed_count": int(row.completed or 0) if row else 0,
                     "average_score": round(float(row.average)) if row and row.average is not None else None})
    return {"data": data}


async def update_run(db: AsyncSession, run_id: int, payload, user_id: int) -> dict:
    run = await _get_run(db, run_id)
    await _ensure_class_manager(db, run.class_id, user_id)
    roles = _normalise_roles(payload.roles)
    keys = {role["key"] for role in roles}
    taken = (await db.execute(select(LmsSimTeamMember.role_key).where(LmsSimTeamMember.run_id == run_id, LmsSimTeamMember.role_key.is_not(None)).distinct())).scalars().all()
    if set(taken) - keys:
        raise ConflictError("A role students have already chosen can be renamed but not removed")
    run.roles, run.min_team_size, run.max_team_size, run.allow_solo = roles, payload.min_team_size, payload.max_team_size, payload.allow_solo
    run.opens_at, run.closes_at, run.xp_per_round, run.points_per_round, run.status = payload.opens_at, payload.closes_at, payload.xp_per_round, payload.points_per_round, payload.status
    await db.commit()
    await db.refresh(run)
    return _run_item(run)


async def _ledger_totals(db: AsyncSession, run_id: int) -> dict[int, tuple[int, int]]:
    rows = await db.execute(select(LmsXpLedger.student_user_id, func.sum(LmsXpLedger.xp), func.sum(LmsXpLedger.points)).where(LmsXpLedger.run_id == run_id).group_by(LmsXpLedger.student_user_id))
    return {student: (int(xp or 0), int(points or 0)) for student, xp, points in rows.all()}


async def run_results(db: AsyncSession, run_id: int, user_id: int) -> dict:
    run = await _get_run(db, run_id)
    await _ensure_class_manager(db, run.class_id, user_id)
    teams = (await db.execute(select(LmsSimTeam).where(LmsSimTeam.run_id == run_id).order_by(LmsSimTeam.created_at))).scalars().all()
    rows = (await db.execute(select(LmsSimTeamMember).where(LmsSimTeamMember.run_id == run_id).order_by(LmsSimTeamMember.joined_at))).scalars().all()
    students = await _class_student_ids(db, run.class_id)
    names = await _names(db, [row.student_user_id for row in rows] + students)
    totals = await _ledger_totals(db, run_id)
    by_team: dict[int, list] = defaultdict(list)
    for row in rows:
        xp, points = totals.get(row.student_user_id, (0, 0))
        by_team[row.team_id].append({"student_user_id": row.student_user_id, "name": names.get(row.student_user_id, f"Student {row.student_user_id}"), "role": _role_name(run, row.role_key), "xp": xp, "points": points})
    placed = {row.student_user_id for row in rows}
    return {
        "run": _run_item(run),
        "teams": [{"team_id": team.team_id, "name": team.name, "is_solo": team.is_solo, "status": _team_status(team, run), "current_round": team.current_round,
                   "total_rounds": len(run.content["rounds"]), "score": _team_score(team, run), "band": team.final_band or engine.band(_team_score(team, run)), "members": by_team[team.team_id]} for team in teams],
        "without_team": sorted((names.get(student, f"Student {student}") for student in students if student not in placed), key=str.lower),
    }


def _decision_log(run: LmsSimRun, results) -> list[dict]:
    log = []
    for result in results:
        round_ = run.content["rounds"][result.round_index]
        for winner in result.winners:
            decision = round_["decisions"][winner["decision_index"]]
            log.append({"round_index": result.round_index, "round_title": round_["title"], "question": decision["question"], "answer": decision["options"][winner["option_index"]]["text"],
                        "votes": winner["votes"], "total_votes": winner["total_votes"], "tied": winner["tied"]})
    return log


async def _results(db: AsyncSession, team_id: int) -> list[LmsSimRoundResult]:
    return list((await db.execute(select(LmsSimRoundResult).where(LmsSimRoundResult.team_id == team_id).order_by(LmsSimRoundResult.round_index))).scalars().all())


async def _messages(db: AsyncSession, team_id: int, viewer_id: int | None, after_id: int = 0, limit: int = 100) -> list[dict]:
    rows = (await db.execute(select(LmsSimMessage).where(LmsSimMessage.team_id == team_id, LmsSimMessage.message_id > after_id).order_by(LmsSimMessage.message_id.desc()).limit(limit))).scalars().all()
    names = await _names(db, [row.sender_user_id for row in rows])
    return [{"message_id": row.message_id, "sender_user_id": row.sender_user_id, "sender": names.get(row.sender_user_id, "Former student"), "body": row.body,
             "created_at": row.created_at, "mine": row.sender_user_id == viewer_id} for row in reversed(rows)]


async def team_review(db: AsyncSession, team_id: int, user_id: int) -> dict:
    team = await db.get(LmsSimTeam, team_id)
    if team is None:
        raise NotFoundError("Team not found")
    run = await _get_run(db, team.run_id)
    await _ensure_class_manager(db, run.class_id, user_id)
    members = await _members(db, team_id)
    names = await _names(db, [member.student_user_id for member in members])
    ledger = (await db.execute(select(LmsXpLedger).where(LmsXpLedger.team_id == team_id).order_by(LmsXpLedger.entry_id.desc()))).scalars().all()
    ledger_names = await _names(db, [entry.student_user_id for entry in ledger])
    return {
        "team_id": team.team_id, "run_id": run.run_id, "name": team.name, "status": _team_status(team, run), "score": _team_score(team, run),
        "band": team.final_band or engine.band(_team_score(team, run)), "current_round": team.current_round, "total_rounds": len(run.content["rounds"]),
        "members": [{"student_user_id": member.student_user_id, "name": names.get(member.student_user_id, f"Student {member.student_user_id}"), "role": _role_name(run, member.role_key)} for member in members],
        "decision_log": _decision_log(run, await _results(db, team_id)),
        "messages": await _messages(db, team_id, None, limit=500),
        "ledger": [{"entry_id": entry.entry_id, "student": ledger_names.get(entry.student_user_id, "Student"), "source_type": entry.source_type, "xp": entry.xp, "points": entry.points, "reason": entry.reason, "created_at": entry.created_at} for entry in ledger],
    }


async def adjust_xp(db: AsyncSession, run_id: int, payload, user_id: int) -> dict:
    run = await _get_run(db, run_id)
    await _ensure_class_manager(db, run.class_id, user_id)
    team_id = payload.team_id
    if team_id is not None:
        team = await db.get(LmsSimTeam, team_id)
        if team is None or team.run_id != run_id:
            raise NotFoundError("Team not found in this simulator")
        targets = [member.student_user_id for member in await _members(db, team_id)]
    else:
        member = (await db.execute(select(LmsSimTeamMember).where(LmsSimTeamMember.run_id == run_id, LmsSimTeamMember.student_user_id == payload.student_user_id))).scalar_one_or_none()
        if member is None:
            raise ValidationError("This student is not on a team in this simulator")
        team_id, targets = member.team_id, [payload.student_user_id]
    reason = payload.reason.strip()
    batch = uuid4().hex
    for student in targets:
        await _award(db, student_user_id=student, run=run, team_id=team_id, source_type="lecturer_adjustment", xp=payload.xp, points=payload.points, reason=reason, event_key=f"sim:{run_id}:adjust:{batch}:{student}", awarded_by=user_id)
    parts = [f"{value:+d} {label}" for value, label in ((payload.xp, "XP"), (payload.points, "points")) if value]
    await notification_service.notify_simulator(db, targets, f"sim:{run_id}:adjust:{batch}", f"Simulator update: {' and '.join(parts)}", f"{run.title}: {reason}", f"class={run.class_id}&run={run_id}")
    await db.commit()
    return {"updated": len(targets)}


# --------------------------------------------------------------------------- student: browse and teams

def _student_team_summary(team: LmsSimTeam, run: LmsSimRun) -> dict:
    return {"team_id": team.team_id, "name": team.name, "is_solo": team.is_solo, "status": _team_status(team, run), "current_round": team.current_round, "total_rounds": len(run.content["rounds"])}


async def student_class_runs(db: AsyncSession, class_id: int, user_id: int) -> dict:
    await _ensure_class_student(db, class_id, user_id)
    runs = (await db.execute(select(LmsSimRun).where(LmsSimRun.class_id == class_id).order_by(LmsSimRun.opens_at.desc()))).scalars().all()
    ids = [run.run_id for run in runs] or [0]
    teams = {team.run_id: team for team in (await db.execute(
        select(LmsSimTeam).join(LmsSimTeamMember, LmsSimTeamMember.team_id == LmsSimTeam.team_id)
        .where(LmsSimTeam.run_id.in_(ids), LmsSimTeamMember.student_user_id == user_id)
    )).scalars().all()}
    invites = dict((await db.execute(select(LmsSimInvite.run_id, func.count()).where(LmsSimInvite.run_id.in_(ids), LmsSimInvite.student_user_id == user_id, LmsSimInvite.status == "pending").group_by(LmsSimInvite.run_id))).all())
    return {"data": [{**_run_item(run), "my_team": _student_team_summary(teams[run.run_id], run) if run.run_id in teams else None, "pending_invites": invites.get(run.run_id, 0)} for run in runs]}


async def student_run_detail(db: AsyncSession, run_id: int, user_id: int) -> dict:
    run = await _get_run(db, run_id)
    await _ensure_class_student(db, run.class_id, user_id)
    student_ids = await _class_student_ids(db, run.class_id)
    placed = {student: team_id for student, team_id in (await db.execute(select(LmsSimTeamMember.student_user_id, LmsSimTeamMember.team_id).where(LmsSimTeamMember.run_id == run_id))).all()}
    team_names = dict((await db.execute(select(LmsSimTeam.team_id, LmsSimTeam.name).where(LmsSimTeam.run_id == run_id))).all())
    my_team_id = placed.get(user_id)
    pending = (await db.execute(select(LmsSimInvite).where(LmsSimInvite.run_id == run_id, LmsSimInvite.status == "pending"))).scalars().all()
    names = await _names(db, set(student_ids) | {invite.invited_by for invite in pending})
    my_invited = {invite.student_user_id: invite.invite_id for invite in pending if invite.team_id == my_team_id}
    received = [{"invite_id": invite.invite_id, "team_id": invite.team_id, "team_name": team_names.get(invite.team_id, "Team"), "from": names.get(invite.invited_by, "A classmate"), "created_at": invite.created_at} for invite in pending if invite.student_user_id == user_id]

    classmates = []
    for student in student_ids:
        if student == user_id:
            continue
        team_id = placed.get(student)
        status = "in_team" if team_id else "invited" if student in my_invited else "available"
        classmates.append({"student_user_id": student, "name": names.get(student, f"Student {student}"), "status": status, "team_name": team_names.get(team_id), "invite_id": my_invited.get(student)})
    classmates.sort(key=lambda item: (item["status"] == "in_team", item["name"].lower()))

    my_team = None
    if my_team_id:
        team = await db.get(LmsSimTeam, my_team_id)
        members = await _members(db, my_team_id)
        member_names = await _names(db, [member.student_user_id for member in members])
        my_team = {**_student_team_summary(team, run), "members": [{"student_user_id": member.student_user_id, "name": member_names.get(member.student_user_id, f"Student {member.student_user_id}"), "role_key": member.role_key, "role": _role_name(run, member.role_key), "me": member.student_user_id == user_id} for member in members], "pending_invites": len(my_invited)}
    return {"run": _run_item(run), "my_team": my_team, "invites_received": received, "classmates": classmates}


async def _cancel_invites_to(db: AsyncSession, run_id: int, student_user_id: int, except_invite: int | None = None) -> None:
    query = update(LmsSimInvite).where(LmsSimInvite.run_id == run_id, LmsSimInvite.student_user_id == student_user_id, LmsSimInvite.status == "pending")
    if except_invite is not None:
        query = query.where(LmsSimInvite.invite_id != except_invite)
    await db.execute(query.values(status="cancelled", responded_at=_now()))


def _validate_role(run: LmsSimRun, role_key: str) -> None:
    if not any(role["key"] == role_key for role in run.roles):
        raise ValidationError("That role is not part of this simulator")


async def create_team(db: AsyncSession, run_id: int, payload, user_id: int) -> dict:
    run = await _get_run(db, run_id)
    await _ensure_class_student(db, run.class_id, user_id)
    _ensure_open(run)
    if payload.solo and not run.allow_solo:
        raise ValidationError("Your lecturer has set this simulator for teams only")
    if payload.role_key:
        _validate_role(run, payload.role_key)
    name = "Solo" if payload.solo else payload.name.strip()
    if not payload.solo and await db.scalar(select(LmsSimTeam.team_id).where(LmsSimTeam.run_id == run_id, func.lower(LmsSimTeam.name) == name.lower())):
        raise ConflictError("Another team in this class already has that name")
    if payload.solo:
        name = (await _names(db, [user_id])).get(user_id, "Solo")
    team = LmsSimTeam(run_id=run_id, name=name, is_solo=payload.solo, status="playing" if payload.solo else "forming", kpi_state=engine.initial_kpi_state(run.content), created_by=user_id, started_at=_now() if payload.solo else None)
    db.add(team)
    try:
        await db.flush()
        role_key = payload.role_key or (run.roles[0]["key"] if payload.solo else None)
        db.add(LmsSimTeamMember(team_id=team.team_id, student_user_id=user_id, run_id=run_id, role_key=role_key))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You are already on a team for this simulator") from exc
    await _cancel_invites_to(db, run_id, user_id)
    await db.commit()
    return await student_run_detail(db, run_id, user_id)


async def _lock_team(db: AsyncSession, team_id: int) -> LmsSimTeam:
    team = (await db.execute(select(LmsSimTeam).where(LmsSimTeam.team_id == team_id).with_for_update())).scalar_one_or_none()
    if team is None:
        raise NotFoundError("Team not found")
    return team


async def _member_context(db: AsyncSession, team_id: int, user_id: int, lock: bool = False) -> tuple[LmsSimTeam, LmsSimRun, LmsSimTeamMember]:
    team = await _lock_team(db, team_id) if lock else await db.get(LmsSimTeam, team_id)
    if team is None:
        raise NotFoundError("Team not found")
    run = await _get_run(db, team.run_id)
    await _ensure_class_student(db, run.class_id, user_id)
    member = await db.get(LmsSimTeamMember, (team_id, user_id))
    if member is None:
        raise ForbiddenError("You are not on this team")
    return team, run, member


async def invite_student(db: AsyncSession, team_id: int, invitee_id: int, user_id: int) -> dict:
    team, run, _ = await _member_context(db, team_id, user_id, lock=True)
    _ensure_open(run)
    if team.is_solo or team.status != "forming":
        raise ValidationError("This team can't take new members now")
    if invitee_id == user_id:
        raise ValidationError("You are already on this team")
    if invitee_id not in await _class_student_ids(db, run.class_id):
        raise ValidationError("You can invite only students in this class")
    current = (await db.execute(select(LmsSimTeamMember.team_id).where(LmsSimTeamMember.run_id == run.run_id, LmsSimTeamMember.student_user_id == invitee_id))).scalar_one_or_none()
    if current is not None:
        other = await db.get(LmsSimTeam, current)
        raise ConflictError(f"This student is already in team \"{other.name}\"")
    members = len(await _members(db, team_id))
    pending = await db.scalar(select(func.count()).select_from(LmsSimInvite).where(LmsSimInvite.team_id == team_id, LmsSimInvite.status == "pending"))
    if members + int(pending or 0) >= run.max_team_size:
        raise ValidationError(f"Teams in this simulator can have at most {run.max_team_size} members, counting pending invites")
    invite = LmsSimInvite(team_id=team_id, run_id=run.run_id, invited_by=user_id, student_user_id=invitee_id)
    db.add(invite)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("This student has already been invited to your team") from exc
    sender = (await _names(db, [user_id])).get(user_id, "A classmate")
    await notification_service.notify_simulator(db, [invitee_id], f"sim:invite:{invite.invite_id}", f"Team invite: {team.name}", f"{sender} invited you to join their team for {run.title}.", f"class={run.class_id}&run={run.run_id}")
    await db.commit()
    return await student_run_detail(db, run.run_id, user_id)


async def respond_invite(db: AsyncSession, invite_id: int, accept: bool, user_id: int) -> dict:
    invite = await db.get(LmsSimInvite, invite_id)
    if invite is None or invite.student_user_id != user_id:
        raise NotFoundError("Invite not found")
    team = await _lock_team(db, invite.team_id)
    await db.refresh(invite)
    run = await _get_run(db, invite.run_id)
    await _ensure_class_student(db, run.class_id, user_id)
    if invite.status != "pending":
        raise ConflictError("This invite is no longer active")
    invite.responded_at = _now()
    if not accept:
        invite.status = "declined"
        await db.commit()
        return await student_run_detail(db, run.run_id, user_id)
    _ensure_open(run)
    if team.status != "forming":
        raise ConflictError("This team has already started")
    if len(await _members(db, team.team_id)) >= run.max_team_size:
        raise ConflictError("This team is full")
    invite.status = "accepted"
    db.add(LmsSimTeamMember(team_id=team.team_id, student_user_id=user_id, run_id=run.run_id))
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("You are already on a team for this simulator") from exc
    await _cancel_invites_to(db, run.run_id, user_id, except_invite=invite_id)
    await db.commit()
    return await student_run_detail(db, run.run_id, user_id)


async def cancel_invite(db: AsyncSession, invite_id: int, user_id: int) -> dict:
    invite = await db.get(LmsSimInvite, invite_id)
    if invite is None:
        raise NotFoundError("Invite not found")
    _, run, _ = await _member_context(db, invite.team_id, user_id, lock=True)
    await db.refresh(invite)
    if invite.status == "pending":
        invite.status, invite.responded_at = "cancelled", _now()
        await db.commit()
    return await student_run_detail(db, run.run_id, user_id)


async def choose_role(db: AsyncSession, team_id: int, role_key: str, user_id: int) -> dict:
    team, run, member = await _member_context(db, team_id, user_id, lock=True)
    if team.status != "forming":
        raise ValidationError("Roles are locked once the team starts")
    _validate_role(run, role_key)
    member.role_key = role_key
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("A teammate has already taken that role") from exc
    return await student_run_detail(db, run.run_id, user_id)


async def leave_team(db: AsyncSession, team_id: int, user_id: int) -> dict:
    team, run, member = await _member_context(db, team_id, user_id, lock=True)
    if team.status != "forming":
        raise ValidationError("You can't leave once the team has started")
    await db.delete(member)
    await db.flush()
    if not await _members(db, team_id):
        await db.execute(delete(LmsSimTeam).where(LmsSimTeam.team_id == team_id))
    await db.commit()
    return await student_run_detail(db, run.run_id, user_id)


async def start_team(db: AsyncSession, team_id: int, user_id: int) -> dict:
    team, run, _ = await _member_context(db, team_id, user_id, lock=True)
    _ensure_open(run)
    if team.status != "forming":
        raise ValidationError("This team has already started")
    members = await _members(db, team_id)
    if len(members) < run.min_team_size:
        raise ValidationError(f"You need at least {run.min_team_size} members to start")
    if any(member.role_key is None for member in members):
        raise ValidationError("Everyone needs to pick a role before you start")
    team.status, team.started_at = "playing", _now()
    await db.execute(update(LmsSimInvite).where(LmsSimInvite.team_id == team_id, LmsSimInvite.status == "pending").values(status="cancelled", responded_at=_now()))
    others = [member.student_user_id for member in members if member.student_user_id != user_id]
    await notification_service.notify_simulator(db, others, f"sim:team:{team_id}:started", f"{team.name} has started", f"Your team has started {run.title}. Round 1 is ready for your vote.", f"class={run.class_id}&run={run.run_id}")
    await db.commit()
    return await student_run_detail(db, run.run_id, user_id)


# --------------------------------------------------------------------------- student: play

async def _close_round(db: AsyncSession, team: LmsSimTeam, run: LmsSimRun, closed_by: int, ballots: list[engine.Ballot]) -> None:
    round_index = team.current_round
    decision_count = len(run.content["rounds"][round_index]["decisions"])
    winners = engine.tally(decision_count, ballots)
    before = team.kpi_state["values"]
    after, delta = engine.resolve_round(run.content, round_index, before, [winner["option_index"] for winner in winners], team.team_id)
    current_score = engine.score(run.content, after)
    db.add(LmsSimRoundResult(team_id=team.team_id, round_index=round_index, winners=winners, kpi_before=before, kpi_after=after, delta=delta, debrief=engine.debrief(run.content, run.tone, round_index, delta), score=current_score, closed_by=closed_by))
    team.kpi_state = {"values": after, "history": [*team.kpi_state["history"], after]}
    team.current_round = round_index + 1
    for student in sorted({ballot.student_user_id for ballot in ballots}):
        await _award(db, student_user_id=student, run=run, team_id=team.team_id, source_type="sim_round", xp=run.xp_per_round, points=run.points_per_round, reason=f"{run.title}: round {round_index + 1}", event_key=f"sim:{team.team_id}:round:{round_index}:{student}")
    if team.current_round >= len(run.content["rounds"]):
        team.status, team.completed_at, team.final_score, team.final_band = "completed", _now(), current_score, engine.band(current_score)
        xp, points = engine.completion_rewards(current_score)
        voters = (await db.execute(select(LmsSimVote.student_user_id).where(LmsSimVote.team_id == team.team_id).distinct())).scalars().all()
        active = set(await _active_member_ids(db, team, run))
        for student in sorted(set(voters) & active):
            await _award(db, student_user_id=student, run=run, team_id=team.team_id, source_type="sim_completion", xp=xp, points=points, reason=f"{run.title}: completed, {team.final_band}", event_key=f"sim:{team.team_id}:complete:{student}")


async def cast_vote(db: AsyncSession, team_id: int, payload, user_id: int) -> dict:
    team, run, _ = await _member_context(db, team_id, user_id, lock=True)
    _ensure_open(run)
    if team.status != "playing":
        raise ValidationError("This team is not playing right now")
    decisions = run.content["rounds"][team.current_round]["decisions"]
    if payload.decision_index >= len(decisions) or payload.option_index >= len(decisions[payload.decision_index]["options"]):
        raise ValidationError("That option does not exist")
    statement = insert(LmsSimVote).values(team_id=team_id, round_index=team.current_round, decision_index=payload.decision_index, student_user_id=user_id, option_index=payload.option_index, voted_at=_now())
    await db.execute(statement.on_conflict_do_update(
        index_elements=["team_id", "round_index", "decision_index", "student_user_id"],
        set_={"option_index": statement.excluded.option_index, "voted_at": statement.excluded.voted_at},
        where=LmsSimVote.option_index != statement.excluded.option_index,
    ))
    members = await _active_member_ids(db, team, run)
    ballots = await _ballots(db, team_id, team.current_round, members)
    if engine.should_auto_close(len(decisions), members, ballots):
        await _close_round(db, team, run, user_id, ballots)
    await db.commit()
    return await _team_state(db, team, run, user_id)


async def close_voting(db: AsyncSession, team_id: int, user_id: int) -> dict:
    team, run, _ = await _member_context(db, team_id, user_id, lock=True)
    _ensure_open(run)
    if team.status != "playing":
        raise ValidationError("This team is not playing right now")
    decisions = run.content["rounds"][team.current_round]["decisions"]
    members = await _active_member_ids(db, team, run)
    ballots = await _ballots(db, team_id, team.current_round, members)
    if not engine.can_close_manually(len(decisions), members, ballots):
        raise ValidationError("More than half the team must vote on every decision first")
    await _close_round(db, team, run, user_id, ballots)
    await db.commit()
    return await _team_state(db, team, run, user_id)


async def _rewards_for(db: AsyncSession, team_id: int, user_id: int) -> dict[str, tuple[int, int]]:
    rows = await db.execute(select(LmsXpLedger.event_key, LmsXpLedger.xp, LmsXpLedger.points).where(LmsXpLedger.team_id == team_id, LmsXpLedger.student_user_id == user_id))
    return {key: (xp, points) for key, xp, points in rows.all()}


async def _team_state(db: AsyncSession, team: LmsSimTeam, run: LmsSimRun, user_id: int, after_message_id: int = 0) -> dict:
    content, values = run.content, team.kpi_state["values"]
    members = await _members(db, team.team_id)
    names = await _names(db, [member.student_user_id for member in members])
    enrolled = set(await _class_student_ids(db, run.class_id))
    active = [member.student_user_id for member in members if member.student_user_id in enrolled]
    status = _team_status(team, run)

    round_payload, my_votes, can_close, complete, hint = None, {}, False, set(), None
    if team.status == "playing" and team.current_round < len(content["rounds"]):
        ballots = await _ballots(db, team.team_id, team.current_round, active)
        decision_count = len(content["rounds"][team.current_round]["decisions"])
        complete = engine.complete_voters(decision_count, active, ballots)
        can_close = status == "playing" and engine.can_close_manually(decision_count, active, ballots)
        round_payload = engine.public_round(content, team.current_round, team.team_id)
        for decision in round_payload["decisions"]:
            for option in decision["options"]:
                option["voters"] = [{"student_user_id": ballot.student_user_id, "name": names.get(ballot.student_user_id, "Teammate")} for ballot in ballots if ballot.decision_index == decision["decision_index"] and ballot.option_index == option["option_index"]]
        my_votes = {ballot.decision_index: ballot.option_index for ballot in ballots if ballot.student_user_id == user_id}
        hint = engine.hint(content, run.tone, team.current_round, values)

    results = await _results(db, team.team_id)
    rewards = await _rewards_for(db, team.team_id, user_id)
    last_result = None
    if results:
        last = results[-1]
        earned = rewards.get(f"sim:{team.team_id}:round:{last.round_index}:{user_id}", (0, 0))
        last_result = {"round_index": last.round_index, "title": content["rounds"][last.round_index]["title"], "delta": last.delta, "debrief": last.debrief, "score": last.score, "decisions": _decision_log(run, [last]), "xp": earned[0], "points": earned[1]}

    report = None
    if team.status == "completed":
        earned = rewards.get(f"sim:{team.team_id}:complete:{user_id}", (0, 0))
        report = {"score": team.final_score, "band": team.final_band, "decision_log": _decision_log(run, results), "reflection_prompts": engine.REFLECTION_PROMPTS.get(run.tone, []), "completion_xp": earned[0], "completion_points": earned[1]}

    live_score = _team_score(team, run)
    return {
        "team": {**_student_team_summary(team, run), "score": live_score, "band": team.final_band or engine.band(live_score)},
        "run": {"run_id": run.run_id, "class_id": run.class_id, "title": run.title, "tone": run.tone, "closes_at": run.closes_at, "window": _window(run)},
        "members": [{"student_user_id": member.student_user_id, "name": names.get(member.student_user_id, "Teammate"), "role": _role_name(run, member.role_key), "me": member.student_user_id == user_id, "voted": member.student_user_id in complete, "active": member.student_user_id in enrolled} for member in members],
        "kpis": [{**kpi, "value": values[kpi["key"]], "history": [point[kpi["key"]] for point in team.kpi_state["history"]]} for kpi in content["kpis"]],
        "round": round_payload, "my_votes": my_votes, "can_close_voting": can_close, "votes_needed": len(active) // 2 + 1, "votes_complete": len(complete), "hint": hint,
        "last_result": last_result, "report": report,
        "my_rewards": {"xp": sum(item[0] for item in rewards.values()), "points": sum(item[1] for item in rewards.values())},
        "messages": await _messages(db, team.team_id, user_id, after_message_id),
    }


async def team_state(db: AsyncSession, team_id: int, user_id: int, after_message_id: int = 0) -> dict:
    team, run, _ = await _member_context(db, team_id, user_id)
    return await _team_state(db, team, run, user_id, after_message_id)


async def post_message(db: AsyncSession, team_id: int, body: str, user_id: int) -> dict:
    team, _, _ = await _member_context(db, team_id, user_id)
    if team.is_solo:
        raise ValidationError("Solo runs have no team chat")
    text = body.strip()
    if not text:
        raise ValidationError("Write a message first")
    recent = await db.scalar(select(func.count()).select_from(LmsSimMessage).where(LmsSimMessage.sender_user_id == user_id, LmsSimMessage.created_at >= _now() - timedelta(minutes=1)))
    if int(recent or 0) >= MESSAGE_LIMIT_PER_MINUTE:
        raise APIError(429, "RATE_LIMITED", "You are sending messages too quickly. Wait a moment and try again.")
    message = LmsSimMessage(team_id=team_id, sender_user_id=user_id, body=text)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return {"message_id": message.message_id, "sender_user_id": user_id, "sender": (await _names(db, [user_id])).get(user_id, "You"), "body": message.body, "created_at": message.created_at, "mine": True}
