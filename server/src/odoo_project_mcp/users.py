from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import IDENTIFIER_RE, ROLE_PERMISSIONS, TokenRegistry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "users": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("users"), list)
    ):
        raise ValueError("The users file must be a version 1 JSON object with a users list.")
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = path.stat() if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, 0o600)
        if previous and hasattr(os, "chown"):
            try:
                os.chown(temporary_name, previous.st_uid, previous.st_gid)
            except PermissionError:
                pass
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _find(users: list[dict[str, Any]], identifier: str) -> dict[str, Any] | None:
    return next((user for user in users if user.get("id") == identifier), None)


def _token() -> tuple[str, str]:
    plaintext = "mcp_" + secrets.token_urlsafe(32)
    return plaintext, hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _expiry(days: int) -> str | None:
    return None if days == 0 else _format_time(_now() + timedelta(days=days))


def _validate_identifier(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("id must contain 1-128 safe email/identifier characters")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage hashed collaborator tokens for Odoo MCP.")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(os.getenv("MCP_USERS_FILE", "/var/lib/odoo-mcp/users.json")),
        help=(
            "Token registry JSON path "
            "(default: MCP_USERS_FILE or /var/lib/odoo-mcp/users.json)."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add an invited collaborator and issue a token once.")
    add.add_argument("id", type=_validate_identifier)
    add.add_argument("--name", required=True)
    add.add_argument("--role", action="append", choices=sorted(ROLE_PERMISSIONS), required=True)
    add.add_argument("--expires-days", type=int, default=180)

    rotate = commands.add_parser("rotate", help="Replace a collaborator token and show it once.")
    rotate.add_argument("id", type=_validate_identifier)
    rotate.add_argument("--expires-days", type=int, default=180)

    revoke = commands.add_parser("revoke", help="Disable a collaborator immediately.")
    revoke.add_argument("id", type=_validate_identifier)

    commands.add_parser("list", help="List collaborators without exposing token material.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "expires_days") and not 0 <= args.expires_days <= 3650:
        parser.error("--expires-days must be between 0 and 3650")
    try:
        data = _read(args.file)
        users = data["users"]
        if args.command == "add":
            if not 1 <= len(args.name.strip()) <= 128:
                raise ValueError("--name must contain 1-128 characters.")
            if _find(users, args.id):
                raise ValueError(f"Collaborator {args.id} already exists; use rotate instead.")
            token, digest = _token()
            timestamp = _format_time(_now())
            users.append(
                {
                    "id": args.id,
                    "name": args.name.strip(),
                    "roles": sorted(set(args.role)),
                    "active": True,
                    "token_sha256": digest,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "expires_at": _expiry(args.expires_days),
                }
            )
            _write(args.file, data)
            TokenRegistry(args.file).validate()
            print(f"Collaborator added: {args.id}")
            print("TOKEN (shown once):")
            print(token)
        elif args.command == "rotate":
            user = _find(users, args.id)
            if not user:
                raise ValueError(f"Collaborator {args.id} does not exist.")
            token, digest = _token()
            user.update(
                {
                    "token_sha256": digest,
                    "active": True,
                    "updated_at": _format_time(_now()),
                    "expires_at": _expiry(args.expires_days),
                }
            )
            _write(args.file, data)
            TokenRegistry(args.file).validate()
            print(f"Token rotated: {args.id}")
            print("TOKEN (shown once):")
            print(token)
        elif args.command == "revoke":
            user = _find(users, args.id)
            if not user:
                raise ValueError(f"Collaborator {args.id} does not exist.")
            user["active"] = False
            user["updated_at"] = _format_time(_now())
            _write(args.file, data)
            TokenRegistry(args.file).validate()
            print(f"Collaborator revoked: {args.id}")
        else:
            TokenRegistry(args.file).validate()
            print("ID\tNAME\tROLES\tACTIVE\tEXPIRES")
            for user in users:
                print(
                    f"{user.get('id', '')}\t{user.get('name', '')}\t"
                    f"{','.join(user.get('roles', []))}\t{user.get('active', False)}\t"
                    f"{user.get('expires_at') or 'never'}"
                )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
