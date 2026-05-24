"""`cra` — operational command-line interface.

Currently one subcommand:

    cra create-admin <email>   Bootstrap the first admin: ensure a Cognito
                               user exists and record a pending invitation
                               granting the 'admin' role on first login.

The HTTP invite endpoint (`POST /api/admin/users`) requires an existing
admin, so this CLI is how the *first* admin is created. Run it inside the
agent container so it inherits the compose DATABASE_URL and AWS creds:

    docker compose -f deploy/compose/docker-compose.yml exec agent \\
        cra create-admin you@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .auth.cognito_admin import CognitoAdminError, create_cognito_user
from .config import get_settings
from .persistence.database import get_db_session, init_db
from .persistence.user_repository import UserRepository


async def _create_admin(email: str, *, skip_cognito: bool) -> int:
    settings = get_settings()
    if not settings.auth_enabled:
        print(
            "ERROR: Cognito is not configured. Set the COGNITO_* env vars "
            "(see scripts/cognito_setup.py) before creating an admin.",
            file=sys.stderr,
        )
        return 1

    email = email.strip().lower()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        print(f"ERROR: {email!r} is not a valid email address.", file=sys.stderr)
        return 1

    await init_db()  # ensure tables + migrations exist

    cognito_status = "skipped"
    if not skip_cognito:
        try:
            cognito_status = await asyncio.to_thread(
                create_cognito_user,
                email,
                region=settings.cognito_region,
                user_pool_id=settings.cognito_user_pool_id,
            )
        except CognitoAdminError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    async with get_db_session() as session:
        await UserRepository(session).create_invitation(email, ["admin"])

    print(f"✓ Admin invitation recorded for {email} (cognito={cognito_status}).")
    print("  Next: have them open /auth/login and sign in — on first login they")
    print("  are provisioned as an admin. If they're already logged in, they must")
    print("  log out and back in for the invitation to be consumed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cra", description="CRA operational CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    ca = sub.add_parser("create-admin", help="Provision the first admin by email")
    ca.add_argument("email", help="Email address to grant the admin role on first login")
    ca.add_argument(
        "--skip-cognito",
        action="store_true",
        help="Don't call Cognito AdminCreateUser (the user is already invited).",
    )

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return asyncio.run(_create_admin(args.email, skip_cognito=args.skip_cognito))
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
