"""Securely set a password for one student-only LMS account."""

import argparse
import asyncio
from getpass import getpass
from pathlib import Path
import sys

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.errors import APIError  # noqa: E402
from app.modules.auth.security import hash_password  # noqa: E402
from app.modules.auth.service import _validate_student_password  # noqa: E402


async def set_password(email: str, password: str) -> None:
    normalized_email = email.strip().lower()
    _validate_student_password(password, normalized_email)
    connection = await asyncpg.connect(
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        ssl="require" if settings.POSTGRES_HOST not in {"localhost", "127.0.0.1"} else None,
    )
    try:
        async with connection.transaction():
            user = await connection.fetchrow(
                "SELECT user_id, is_active FROM users WHERE LOWER(email) = $1 FOR UPDATE",
                normalized_email,
            )
            if user is None or not user["is_active"]:
                raise RuntimeError("Active user not found; no credential was changed.")
            access_rows = await connection.fetch(
                """
                SELECT al.access_key
                FROM user_access_levels ual
                JOIN access_levels al ON al.access_level_id = ual.access_level_id
                WHERE ual.user_id = $1 AND al.is_active = TRUE
                """,
                user["user_id"],
            )
            access_keys = {row["access_key"] for row in access_rows}
            if access_keys != {"LMS", "STUDENT"}:
                raise RuntimeError(
                    "Password sign-in is restricted to student-only LMS accounts; no credential was changed."
                )
            password_hash = hash_password(password)
            await connection.execute(
                """
                INSERT INTO password_credentials
                    (user_id, password_hash, failed_attempts, locked_until, verified_at)
                VALUES ($1, $2, 0, NULL, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    failed_attempts = 0,
                    locked_until = NULL,
                    verified_at = NOW(),
                    updated_at = NOW()
                """,
                user["user_id"], password_hash,
            )
            await connection.execute(
                "DELETE FROM authenticator_setup_tokens WHERE user_id = $1 AND used_at IS NULL",
                user["user_id"],
            )
            await connection.execute(
                "UPDATE authenticator_credentials SET enabled = FALSE WHERE user_id = $1",
                user["user_id"],
            )
            await connection.execute(
                "DELETE FROM authenticator_recovery_codes WHERE user_id = $1",
                user["user_id"],
            )
            await connection.execute(
                "DELETE FROM refresh_tokens WHERE user_id = $1",
                user["user_id"],
            )
    finally:
        await connection.close()
    print(f"Student password updated and existing sessions revoked for {normalized_email}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    args = parser.parse_args()
    password = getpass("New student password: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match; nothing was changed.")
    try:
        asyncio.run(set_password(args.email, password))
    except APIError as error:
        raise SystemExit(f"{error.message} Nothing was changed.") from error


if __name__ == "__main__":
    main()
