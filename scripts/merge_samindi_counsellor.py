"""Consolidate the duplicate legacy counsellor into Samindi's real CRM account.

Preview by default. ``--apply`` transfers CRM lead ownership and removes only
the disposable profile created by the Recorrected pipeline import. The separate
student account is untouched.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.database import engine


REAL_USER_ID = 179
LEGACY_USER_ID = 225
REAL_EMAIL = "samindi@inspire.college"
LEGACY_EMAIL = "samindi.weerasiri.legacy@example.com"
DISPLAY_NAME = "Samindi Weerasiri"


async def main(apply: bool):
    engine.echo = False
    async with engine.begin() as db:
        users = (await db.execute(text("""
            SELECT user_id, full_name, email, is_active FROM users
            WHERE user_id IN (:real, :legacy) ORDER BY user_id
        """), {"real": REAL_USER_ID, "legacy": LEGACY_USER_ID})).mappings().all()
        by_id = {row["user_id"]: row for row in users}
        if set(by_id) != {REAL_USER_ID, LEGACY_USER_ID}:
            raise ValueError("The two expected accounts were not found")
        if by_id[REAL_USER_ID]["email"].casefold() != REAL_EMAIL:
            raise ValueError("The real Samindi account email changed")
        if by_id[LEGACY_USER_ID]["email"].casefold() != LEGACY_EMAIL:
            raise ValueError("The disposable Samindi account email changed")
        counts = (await db.execute(text("""
            SELECT u.user_id,
                   (SELECT count(*) FROM crm_leads WHERE assigned_counsellor_id=u.user_id) AS leads,
                   (SELECT count(*) FROM password_credentials WHERE user_id=u.user_id) AS passwords,
                   (SELECT count(*) FROM refresh_tokens WHERE user_id=u.user_id) AS tokens,
                   (SELECT count(*) FROM lms_student_profiles WHERE user_id=u.user_id) AS student_profiles
            FROM users u WHERE u.user_id IN (:real, :legacy) ORDER BY u.user_id
        """), {"real": REAL_USER_ID, "legacy": LEGACY_USER_ID})).mappings().all()
        by_count = {row["user_id"]: row for row in counts}
        if by_count[LEGACY_USER_ID]["passwords"] or by_count[LEGACY_USER_ID]["tokens"] or by_count[LEGACY_USER_ID]["student_profiles"]:
            raise ValueError("Disposable profile has acquired login or student data")
        print(json.dumps({"real_account": dict(by_id[REAL_USER_ID]),
                          "duplicate_account": dict(by_id[LEGACY_USER_ID]),
                          "current_leads": by_count[REAL_USER_ID]["leads"],
                          "leads_to_transfer": by_count[LEGACY_USER_ID]["leads"],
                          "student_account_untouched": 50}, indent=2))
        if not apply:
            print("Preview only. No accounts or leads changed.")
            return
        if by_count[LEGACY_USER_ID]["leads"] != 2632:
            raise ValueError("Lead assignment count changed; no merge applied")
        await db.execute(text("""
            UPDATE crm_leads SET assigned_counsellor_id=:real,
                   counsellor_name=:display
            WHERE assigned_counsellor_id=:legacy
        """), {"real": REAL_USER_ID, "legacy": LEGACY_USER_ID, "display": DISPLAY_NAME})
        await db.execute(text("""
            UPDATE crm_leads SET counsellor_name=:display
            WHERE assigned_counsellor_id=:real
        """), {"real": REAL_USER_ID, "display": DISPLAY_NAME})
        await db.execute(text("UPDATE users SET full_name=:display WHERE user_id=:real"),
                         {"display": DISPLAY_NAME, "real": REAL_USER_ID})
        await db.execute(text("DELETE FROM crm_counsellor_status WHERE user_id=:legacy"),
                         {"legacy": LEGACY_USER_ID})
        await db.execute(text("DELETE FROM user_access_levels WHERE user_id=:legacy"),
                         {"legacy": LEGACY_USER_ID})
        await db.execute(text("DELETE FROM users WHERE user_id=:legacy"),
                         {"legacy": LEGACY_USER_ID})
        print("Transferred 2,632 leads to the real account and removed the duplicate profile.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
