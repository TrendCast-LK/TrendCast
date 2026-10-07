"""Create an admin dashboard account, or reset/disable an existing one.

There is no signup route for admins: this script, run by someone with the
database URL, is the only way to make one.

Usage (from backend/):
    python -m tools.create_admin --email you@example.com --name "Your Name"
    python -m tools.create_admin --email you@example.com --reset-password
    python -m tools.create_admin --email you@example.com --disable
    python -m tools.create_admin --email you@example.com --enable

The password is prompted for (never passed on the command line). Uses
SUPABASE_DB_URL from backend/.env unless --db-url is given.

Exit status: 0 = done, 1 = refused (bad input, account exists/missing), 2 = could not run.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import bcrypt
import psycopg2

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    # Same scheme as security.hash_password, without importing config (which
    # needs every backend secret just to create an account).
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def validate_password(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > 72:
        return "Password must be at most 72 bytes (bcrypt's limit)."
    return None


def create_admin(conn, email: str, full_name: str, password: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO admins (full_name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (full_name, email.strip().lower(), hash_password(password)),
        )
        admin_id = cur.fetchone()[0]
    conn.commit()
    return admin_id


def reset_password(conn, email: str, password: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE admins SET password_hash = %s WHERE email = %s",
            (hash_password(password), email.strip().lower()),
        )
        changed = cur.rowcount == 1
    conn.commit()
    return changed


def set_active(conn, email: str, active: bool) -> bool:
    with conn.cursor() as cur:
        cur.execute("UPDATE admins SET is_active = %s WHERE email = %s", (active, email.strip().lower()))
        changed = cur.rowcount == 1
    conn.commit()
    return changed


def _prompt_password() -> str | None:
    password = getpass.getpass("Password: ")
    problem = validate_password(password)
    if problem:
        print(problem, file=sys.stderr)
        return None
    if getpass.getpass("Repeat password: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return None
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", help="full name (required when creating)")
    parser.add_argument("--db-url", help="Postgres URL (default: SUPABASE_DB_URL)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reset-password", action="store_true")
    mode.add_argument("--disable", action="store_true")
    mode.add_argument("--enable", action="store_true")
    args = parser.parse_args(argv)

    db_url = args.db_url
    if not db_url:
        from dotenv import load_dotenv

        load_dotenv()
        db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("No database URL: set SUPABASE_DB_URL or pass --db-url.", file=sys.stderr)
        return 2

    try:
        conn = psycopg2.connect(db_url)
    except psycopg2.Error as exc:
        print(f"Could not connect to the database: {exc}", file=sys.stderr)
        return 2

    try:
        if args.disable or args.enable:
            if not set_active(conn, args.email, active=args.enable):
                print(f"No admin with email {args.email}.", file=sys.stderr)
                return 1
            print(f"Admin {args.email} {'enabled' if args.enable else 'disabled'}.")
            return 0

        password = _prompt_password()
        if password is None:
            return 1

        if args.reset_password:
            if not reset_password(conn, args.email, password):
                print(f"No admin with email {args.email}.", file=sys.stderr)
                return 1
            print(f"Password reset for {args.email}.")
            return 0

        if not args.name:
            print("--name is required when creating an admin.", file=sys.stderr)
            return 1
        try:
            admin_id = create_admin(conn, args.email, args.name, password)
        except psycopg2.errors.UniqueViolation:
            print(f"An admin with email {args.email} already exists (use --reset-password).", file=sys.stderr)
            return 1
        except psycopg2.errors.UndefinedTable:
            print("The admins table is missing: apply migrations/006_admin_dashboard.sql first.", file=sys.stderr)
            return 2
        print(f"Created admin #{admin_id} ({args.email.strip().lower()}).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
