import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from starlette.responses import JSONResponse

from odoo_project_mcp.auth import (
    AuthenticationConfigurationError,
    BearerTokenMiddleware,
    TokenRegistry,
    current_principal,
)


def write_users(path: Path, token: str, *, active: bool = True, roles=None) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "users": [
                    {
                        "id": "developer@gmail.com",
                        "name": "Developer",
                        "roles": roles or ["developer"],
                        "active": active,
                        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_registry_authenticates_hash_and_applies_role(tmp_path):
    token = "mcp_" + "a" * 43
    path = tmp_path / "users.json"
    write_users(path, token)
    registry = TokenRegistry(path)

    principal = registry.authenticate(token)

    assert principal is not None
    assert principal.identifier == "developer@gmail.com"
    assert principal.can("tasks:write")
    assert not principal.can("tasks:verify")
    assert registry.authenticate("mcp_" + "b" * 43) is None


def test_registry_rejects_plaintext_and_unsafe_permissions(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "users": [
                    {
                        "id": "user@gmail.com",
                        "name": "User",
                        "roles": ["reader"],
                        "active": True,
                        "token": "must-not-be-stored",
                        "token_sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    with pytest.raises(AuthenticationConfigurationError, match="plaintext token"):
        TokenRegistry(path).validate()

    path.chmod(0o644)
    with pytest.raises(AuthenticationConfigurationError, match="mode 600"):
        TokenRegistry(path).validate()


def test_registry_hot_reloads_revocation(tmp_path):
    token = "mcp_" + "c" * 43
    path = tmp_path / "users.json"
    write_users(path, token)
    registry = TokenRegistry(path)
    assert registry.authenticate(token) is not None

    write_users(path, token, active=False)
    current = path.stat().st_mtime_ns
    os.utime(path, ns=(current + 1_000_000, current + 1_000_000))

    assert registry.authenticate(token) is None


def test_bearer_middleware_rejects_invalid_and_sets_principal(tmp_path):
    token = "mcp_" + "d" * 43
    path = tmp_path / "users.json"
    write_users(path, token)
    registry = TokenRegistry(path)

    async def inner(scope, receive, send):
        principal = current_principal()
        response = JSONResponse({"actor": principal.identifier if principal else None})
        await response(scope, receive, send)

    app = BearerTokenMiddleware(inner, registry, requests_per_minute=10)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://mcp.test") as client:
            unauthorized = await client.get("/mcp")
            authorized = await client.get(
                "/mcp", headers={"Authorization": f"Bearer {token}"}
            )
        return unauthorized, authorized

    unauthorized, authorized = asyncio.run(exercise())
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"].startswith("Bearer")
    assert authorized.status_code == 200
    assert authorized.json() == {"actor": "developer@gmail.com"}
    assert authorized.headers["cache-control"] == "no-store"
