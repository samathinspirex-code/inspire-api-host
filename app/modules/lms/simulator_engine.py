"""Simulator rules with no database access: majority votes, KPI movement, score, debrief and rewards."""
import hashlib
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

COMPLETION_POINTS = {"Distinction": 100, "Merit": 70, "Pass": 40, "Refer": 20}
REFLECTION_PROMPTS = {
    "student": ["Which decision are you proudest of, and why?", "Where did a quick win cost you later?", "What would you do differently with the plot twists?"],
    "professional": ["Which theoretical framework best explains your strategy?", "Evaluate one trade-off you made against an alternative.", "How did external shocks change your risk appetite?"],
}


@dataclass(frozen=True)
class Ballot:
    student_user_id: int
    decision_index: int
    option_index: int
    voted_at: datetime


def _rng(*parts: object) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:16], 16))


def initial_kpi_state(content: dict) -> dict:
    values = {kpi["key"]: float(kpi["start"]) for kpi in content["kpis"]}
    return {"values": values, "history": [dict(values)]}


def score(content: dict, values: dict) -> int:
    total = 50.0
    for kpi in content["kpis"]:
        total += kpi["direction"] * (float(values[kpi["key"]]) - float(kpi["start"])) / float(kpi["scale"]) * 8
    return max(0, min(100, math.floor(total + 0.5)))


def band(value: int) -> str:
    return "Distinction" if value >= 75 else "Merit" if value >= 60 else "Pass" if value >= 45 else "Refer"


def complete_voters(decision_count: int, member_ids: Iterable[int], ballots: Iterable[Ballot]) -> set[int]:
    """Members who have voted on every decision in the round."""
    decided: dict[int, set[int]] = {}
    for ballot in ballots:
        decided.setdefault(ballot.student_user_id, set()).add(ballot.decision_index)
    return {member for member in member_ids if len(decided.get(member, ())) >= decision_count}


def should_auto_close(decision_count: int, member_ids: Sequence[int], ballots: Iterable[Ballot]) -> bool:
    return bool(member_ids) and len(complete_voters(decision_count, member_ids, ballots)) == len(member_ids)


def can_close_manually(decision_count: int, member_ids: Sequence[int], ballots: Iterable[Ballot]) -> bool:
    return len(complete_voters(decision_count, member_ids, ballots)) * 2 > len(member_ids)


def tally(decision_count: int, ballots: Iterable[Ballot]) -> list[dict]:
    """Winning option per decision. On a tie, the tied option that reached that count first wins."""
    grouped: dict[int, dict[int, list[datetime]]] = {}
    for ballot in ballots:
        grouped.setdefault(ballot.decision_index, {}).setdefault(ballot.option_index, []).append(ballot.voted_at)
    results = []
    for decision_index in range(decision_count):
        options = grouped.get(decision_index)
        if not options:
            raise ValueError("Every decision needs at least one vote")
        top = max(len(times) for times in options.values())
        tied = {option: sorted(times)[top - 1] for option, times in options.items() if len(times) == top}
        winner = min(tied, key=lambda option: (tied[option], option))
        results.append({
            "decision_index": decision_index, "option_index": winner, "votes": top,
            "total_votes": sum(len(times) for times in options.values()), "tied": len(tied) > 1,
        })
    return results


def resolve_round(content: dict, round_index: int, values: dict, picks: Sequence[int], team_id: int) -> tuple[dict, dict]:
    """Applies winning options, the plot-twist penalty, then small seeded noise. Returns new values and the change."""
    kpis, round_ = content["kpis"], content["rounds"][round_index]
    current = {key: float(value) for key, value in values.items()}
    before = dict(current)
    rng = _rng(team_id, round_index, list(picks))
    for decision, pick in zip(round_["decisions"], picks):
        for key, change in decision["options"][pick]["effects"].items():
            current[key] += float(change)
    if round_.get("event"):
        hit = kpis[math.floor(rng.random() * len(kpis))]
        current[hit["key"]] -= hit["direction"] * float(hit["scale"]) * 0.25 * (0.5 + rng.random())
    for kpi in kpis:
        current[kpi["key"]] += float(kpi["scale"]) * (rng.random() - 0.5) * 0.12
        if kpi.get("suffix") in {"%", "/100"}:
            current[kpi["key"]] = max(0.0, min(100.0, current[kpi["key"]]))
    current = {key: round(value, 4) for key, value in current.items()}
    delta = {kpi["key"]: round(current[kpi["key"]] - before[kpi["key"]], int(kpi.get("decimals") or 0)) for kpi in kpis}
    return current, delta


def debrief(content: dict, tone: str, round_index: int, delta: dict) -> str:
    kpis = content["kpis"]
    ranked = sorted(((kpi, kpi["direction"] * delta[kpi["key"]] / float(kpi["scale"])) for kpi in kpis), key=lambda item: -item[1])
    best, (worst, worst_value) = ranked[0][0]["label"].lower(), (ranked[-1][0]["label"].lower(), ranked[-1][1])
    has_event = bool(content["rounds"][round_index].get("event"))
    if tone == "student":
        follow = f"But {worst} took a hit. Next round, look for a choice that fixes that without undoing your win." if worst_value < 0 else "Nothing went backwards this round. Clean."
        return f"Good move on {best}. {follow}{' The plot twist shook things up a bit; that is real life.' if has_event else ''}"
    follow = f", at the expense of {worst}. Reflect on whether that trade-off was deliberate and how you would explain it to the board." if worst_value < 0 else ", with no material deterioration elsewhere."
    return f"Your decisions strengthened {best}{follow}{' The external development has been reflected in the results.' if has_event else ''}"


def hint(content: dict, tone: str, round_index: int, values: dict) -> str:
    weakest = min(content["kpis"], key=lambda kpi: kpi["direction"] * (float(values[kpi["key"]]) - float(kpi["start"])) / float(kpi["scale"]))
    question = content["rounds"][round_index]["decisions"][0]["question"]
    if tone == "student":
        return f"Real talk: your weakest area is {weakest['label'].lower()}. Look for the option in \"{question}\" that protects it. Cheap wins now can cost you later."
    return f"Consider: your weakest area is {weakest['label'].lower()}. Look for the option in \"{question}\" that protects it, including how stakeholders will read the signal."


def public_round(content: dict, round_index: int, team_id: int) -> dict:
    """The round as students may see it: option effects removed and options in a stable per-team order."""
    round_ = content["rounds"][round_index]
    decisions = []
    for decision_index, decision in enumerate(round_["decisions"]):
        order = list(range(len(decision["options"])))
        _rng(team_id, round_index, decision_index, "order").shuffle(order)
        decisions.append({
            "decision_index": decision_index, "question": decision["question"],
            "options": [{"option_index": index, "text": decision["options"][index]["text"]} for index in order],
        })
    return {"round_index": round_index, "title": round_["title"], "brief": round_["brief"], "event": round_.get("event"), "decisions": decisions}


def default_how_it_works(tone: str, roles: list[dict], max_team_size: int, allow_solo: bool, round_count: int) -> list[dict]:
    """The design's four "How it works" rows, worded from the class's real settings."""
    names = ", ".join(role["name"] for role in roles)
    mates = max(max_team_size - 1, 1)
    if tone == "student":
        team = ("Solo or squad", f"Play alone, or with up to {mates} {"classmate" if mates == 1 else "classmates"} in roles: {names}.") if allow_solo else ("Squad up", f"Team up with up to {mates} {"classmate" if mates == 1 else "classmates"} in roles: {names}.")
        return [
            {"title": f"{round_count} rounds across the term", "text": "Each round brings a new situation and a plot twist."},
            {"title": team[0], "text": team[1]},
            {"title": "Your mentor debriefs you", "text": "After every round you see what moved your KPIs and why."},
            {"title": "It counts", "text": "Every round earns XP and points for your class leaderboard. Your score maps to Pass, Merit or Distinction."},
        ]
    team = ("Individual or syndicate", f"Work individually, or with up to {mates} {"colleague" if mates == 1 else "colleagues"} in roles: {names}.") if allow_solo else ("Syndicate", f"Work with up to {mates} {"colleague" if mates == 1 else "colleagues"} in roles: {names}.")
    return [
        {"title": f"{round_count} decision rounds", "text": "Each round brings a new situation and an external shock."},
        {"title": team[0], "text": team[1]},
        {"title": "Mentor debrief", "text": "After every round you see what moved your KPIs and why."},
        {"title": "Assessed outcome", "text": "Each round earns XP and points. Your final score maps to Pass, Merit or Distinction."},
    ]


def rank(rows: list[dict]) -> list[dict]:
    """Orders started teams by live score, then rounds completed, then who started first."""
    ordered = sorted(rows, key=lambda row: (-row["score"], -row["rounds_done"], row["started_at"]))
    return [{**row, "rank": index} for index, row in enumerate(ordered, start=1)]


def completion_rewards(final_score: int) -> tuple[int, int]:
    return final_score * 2, COMPLETION_POINTS[band(final_score)]
