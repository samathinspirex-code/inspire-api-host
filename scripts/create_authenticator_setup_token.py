"""Create the first single-use Google Authenticator setup token.

Use this only when no signed-in administrator is available. Once an
administrator has enrolled, setup tokens should be generated in CMS User
Management instead.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.modules.auth.repository import UserRepository  # noqa: E402
from app.modules.auth.service import issue_authenticator_setup_token  # noqa: E402


async def create_token(email: str) -> int:
    async with AsyncSessionLocal() as db:
        user = await UserRepository(db).get_by_email(email.strip().lower())
        if user is None or not user.is_active:
            print("Active user not found.", file=sys.stderr)
            return 1
        result = await issue_authenticator_setup_token(db, user.user_id, created_by=None)
        print(f"Email: {result.email}")
        print(f"Setup token: {result.setup_token}")
        print(f"Expires at: {result.expires_at.isoformat()}")
        print("Open CMS or LMS login, choose First-time setup, and use this token once.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an Authenticator setup token")
    parser.add_argument("email", help="Email address of the existing CMS/LMS user")
    args = parser.parse_args()
    return asyncio.run(create_token(args.email))


if __name__ == "__main__":
    raise SystemExit(main())
