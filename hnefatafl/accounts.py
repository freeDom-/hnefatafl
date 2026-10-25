"""Local OS-authorized account administration; never mounted in the HTTP API."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import sys
import warnings
from pathlib import Path

from .auth import Auth, SecuritySettings
from .storage import Store


def read_password(prompt: str) -> str:
    # getpass otherwise warns and falls back to potentially echoed input.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise ValueError("Use an interactive terminal that can hide password input") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "migrate",
            "init-admin",
            "create-user",
            "reset-password",
            "set-role",
            "disable",
            "enable",
            "check-collisions",
        ),
    )
    parser.add_argument("--data-dir", default=os.environ.get("HNEFATAFL_DATA_DIR", "data"))
    parser.add_argument("--username")
    parser.add_argument("--role", choices=("USER", "ADMIN"))
    parser.add_argument(
        "--credentials-stdin",
        action="store_true",
        help="Read a single JSON object from protected stdin, never argv",
    )
    args = parser.parse_args(argv)
    root = Path(args.data_dir).resolve()
    if args.command == "check-collisions":
        from .account_migrations import check_collisions

        try:
            report = check_collisions(root / "hnefatafl.sqlite3")
            print(json.dumps(report))
            return 0 if report["ok"] else 1
        except sqlite3.Error:
            print("Cannot open the account database read-only.", file=sys.stderr)
            return 1
    root.mkdir(parents=True, exist_ok=True)
    if os.geteuid() not in (0, root.stat().st_uid):
        parser.error("Run as root or the owner of the application data directory")
    os.umask(0o077)
    store = Store(root / "hnefatafl.sqlite3")
    if args.command == "migrate":
        from .storage import SCHEMA_VERSION

        print(f"Database schema {SCHEMA_VERSION} ready.")
        return 0
    auth = Auth(store, SecuritySettings())
    try:
        if args.command == "init-admin":
            with store.connection() as c:
                if c.execute("SELECT 1 FROM auth_meta WHERE key='bootstrapped'").fetchone():
                    print("Initial administrator already provisioned; no changes made.")
                    return 0
        if args.command in {"init-admin", "create-user"}:
            if args.credentials_stdin:
                value = json.loads(sys.stdin.read(16385))
                username, email, password = value["username"], value["email"], value["password"]
            else:
                username = (
                    args.username
                    or input(
                        "Administrator username [Odin]: "
                        if args.command == "init-admin"
                        else "Username: "
                    ).strip()
                    or "Odin"
                )
                email = input("Email address: ").strip()
                password = read_password("Password (at least 12 characters): ")
                if password != read_password("Confirm password: "):
                    raise ValueError("Passwords do not match")
            account = auth.create_account(
                username,
                email,
                password,
                "ADMIN" if args.command == "init-admin" else "USER",
                bootstrap=args.command == "init-admin",
            )
            print(f"Created {account['role']} account {account['username']}.")
        else:
            username = args.username or input("Account username: ").strip()
            with store.connection() as c:
                row = c.execute(
                    "SELECT id FROM accounts WHERE username=? COLLATE NOCASE", (username,)
                ).fetchone()
            if row is None:
                raise ValueError("Account not found")
            changes: dict = {}
            if args.command == "reset-password":
                password = read_password("New password: ")
                if password != read_password("Confirm password: "):
                    raise ValueError("Passwords do not match")
                changes["password"] = password
            elif args.command == "set-role":
                if args.role is None:
                    raise ValueError("--role is required")
                changes["role"] = args.role
            else:
                changes["active"] = args.command == "enable"
            auth.update_account(row["id"], **changes)
            print("Account updated; all its sessions revoked.")
        return 0
    except sqlite3.IntegrityError:
        print("Username or email is already in use.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError) as error:
        print(f"Account operation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
