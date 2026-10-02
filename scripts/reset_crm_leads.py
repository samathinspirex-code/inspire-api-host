"""Back up and clear CRM lead data at the verified production cutover.

Requires a protected backup directory outside the repository. Without --apply,
this command only reports counts. Public intake waits while the transaction holds
the CRM table locks, then resumes against the empty CRM.
"""
import argparse
import asyncio
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text
from app.core.database import engine

TABLES = ("crm_activities", "crm_leads", "crm_admission_leads")


async def run(backup_dir: Path, apply: bool):
    repo = Path(__file__).resolve().parents[1]
    backup_dir = backup_dir.resolve()
    if backup_dir == repo or repo in backup_dir.parents:
        raise SystemExit("Choose a protected backup directory outside the repository")
    if not backup_dir.is_dir():
        raise SystemExit("Backup directory does not exist")

    async with engine.begin() as connection:
        await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        if apply:
            await connection.execute(text("LOCK TABLE crm_activities, crm_leads, crm_admission_leads IN ACCESS EXCLUSIVE MODE"))
        counts = {table: int(await connection.scalar(text(f"SELECT count(*) FROM {table}"))) for table in TABLES}
        print(json.dumps({"current_counts": counts, "will_clear": apply}))
        if not apply:
            return

        snapshot = {table: [dict(row) for row in (await connection.execute(text(f"SELECT * FROM {table}"))).mappings()] for table in TABLES}
        if any(len(snapshot[table]) != counts[table] for table in TABLES):
            raise RuntimeError("Backup count does not match source count; no records cleared")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"crm-leads-before-rebuild-{stamp}.json.gz"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb") as compressed:
                    compressed.write(json.dumps({"created_at": stamp, "counts": counts, "tables": snapshot}, default=str).encode("utf-8"))
                raw.flush()
                os.fsync(raw.fileno())
            with gzip.open(target, "rt", encoding="utf-8") as saved:
                verified = json.load(saved)
            if verified["counts"] != counts or any(len(verified["tables"][table]) != counts[table] for table in TABLES):
                raise RuntimeError("Backup verification failed; no records cleared")
            await connection.execute(text("DELETE FROM crm_activities"))
            await connection.execute(text("DELETE FROM crm_leads"))
            await connection.execute(text("DELETE FROM crm_admission_leads"))
            remaining = {table: int(await connection.scalar(text(f"SELECT count(*) FROM {table}"))) for table in TABLES}
            if any(remaining.values()):
                raise RuntimeError("CRM was not empty; transaction rolled back")
            print(json.dumps({"backup": str(target), "verified_counts": counts, "remaining": remaining}))
        except Exception:
            if target.exists():
                print(f"Backup retained at {target}; CRM deletion rolled back")
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.backup_dir, arguments.apply))
