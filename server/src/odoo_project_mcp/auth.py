from __future__ import annotations

import contextvars
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import stat
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = logging.getLogger(__name__)

ROLE_PERMISSIONS = {
    "reader": frozenset({"projects:read", "tasks:read"}),
    "developer": frozenset(
        {"projects:read", "tasks:read", "tasks:comment", "tasks:write"}
    ),
    "reviewer": frozenset(
        {"projects:read", "tasks:read", "tasks:comment", "tasks:verify"}
    ),
    "admin": frozenset(
        {
            "projects:read",
            "tasks:read",
            "tasks:comment",
            "tasks:write",
            "tasks:verify",
        }
    ),
}

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.@+-]{1,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_USERS_FILE_BYTES = 1024 * 1024


class AuthenticationConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Principal:
    identifier: str
    name: str
    roles: frozenset[str]
    token_digest: str
    expires_at: datetime | None = None

    @property
    def permissions(self) -> frozenset[str]:
        permissions: set[str] = set()
        for role in self.roles:
            permissions.update(ROLE_PERMISSIONS[role])
        return frozenset(permissions)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


_current_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "odoo_project_mcp_principal", default=None
)


def current_principal() -> Principal | None:
    return _current_principal.get()


def _parse_datetime(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise AuthenticationConfigurationError(f"{field} must be an ISO-8601 string or null.")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AuthenticationConfigurationError(f"{field} is not a valid ISO-8601 time.") from exc
    if parsed.tzinfo is None:
        raise AuthenticationConfigurationError(f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


class TokenRegistry:
    """Reloadable registry containing only SHA-256 token digests, never plaintext tokens."""

    def __init__(self, path: Path, *, strict_permissions: bool = True):
        self.path = path
        self.strict_permissions = strict_permissions
        self._lock = threading.Lock()
        self._fingerprint: tuple[int, int] | None = None
        self._principals: tuple[Principal, ...] = ()

    def validate(self) -> None:
        self._reload(force=True)

    def authenticate(self, token: str) -> Principal | None:
        if not 20 <= len(token) <= 512:
            return None
        self._reload()
        supplied_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched: Principal | None = None
        for principal in self._principals:
            if hmac.compare_digest(principal.token_digest, supplied_digest):
                matched = principal
        if matched and matched.expires_at and matched.expires_at <= datetime.now(timezone.utc):
            return None
        return matched

    def _reload(self, *, force: bool = False) -> None:
        try:
            file_stat = self.path.stat()
        except OSError as exc:
            raise AuthenticationConfigurationError(
                f"Cannot read MCP users file {self.path}: {exc}"
            ) from exc

        fingerprint = (file_stat.st_mtime_ns, file_stat.st_size)
        if not force and fingerprint == self._fingerprint:
            return
        with self._lock:
            file_stat = self.path.stat()
            fingerprint = (file_stat.st_mtime_ns, file_stat.st_size)
            if not force and fingerprint == self._fingerprint:
                return
            if file_stat.st_size > MAX_USERS_FILE_BYTES:
                raise AuthenticationConfigurationError("MCP users file exceeds 1 MiB.")
            if self.strict_permissions and os.name == "posix":
                mode = stat.S_IMODE(file_stat.st_mode)
                if mode & 0o077:
                    raise AuthenticationConfigurationError(
                        f"MCP users file {self.path} must have mode 600 or stricter."
                    )
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise AuthenticationConfigurationError(
                    f"MCP users file is not valid JSON: {exc}"
                ) from exc
            principals = self._parse(data)
            self._principals = tuple(principals)
            self._fingerprint = fingerprint
            _logger.info("Loaded %s active MCP collaborator(s)", len(principals))

    @staticmethod
    def _parse(data: Any) -> list[Principal]:
        if not isinstance(data, dict) or data.get("version") != 1:
            raise AuthenticationConfigurationError("MCP users file must have version 1.")
        users = data.get("users")
        if not isinstance(users, list):
            raise AuthenticationConfigurationError("MCP users file must contain a users list.")

        identifiers: set[str] = set()
        digests: set[str] = set()
        principals: list[Principal] = []
        for index, entry in enumerate(users):
            prefix = f"users[{index}]"
            if not isinstance(entry, dict):
                raise AuthenticationConfigurationError(f"{prefix} must be an object.")
            if "token" in entry:
                raise AuthenticationConfigurationError(
                    f"{prefix} contains a plaintext token; only token_sha256 is allowed."
                )
            identifier = entry.get("id")
            name = entry.get("name")
            digest = entry.get("token_sha256")
            roles = entry.get("roles")
            active = entry.get("active", True)
            if not isinstance(identifier, str) or not IDENTIFIER_RE.fullmatch(identifier):
                raise AuthenticationConfigurationError(f"{prefix}.id is invalid.")
            if identifier in identifiers:
                raise AuthenticationConfigurationError(f"Duplicate collaborator id: {identifier}.")
            identifiers.add(identifier)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 128:
                raise AuthenticationConfigurationError(f"{prefix}.name is invalid.")
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                raise AuthenticationConfigurationError(f"{prefix}.token_sha256 is invalid.")
            if digest in digests:
                raise AuthenticationConfigurationError(f"Duplicate token digest in {prefix}.")
            digests.add(digest)
            if not isinstance(roles, list) or not roles:
                raise AuthenticationConfigurationError(f"{prefix}.roles must be a non-empty list.")
            role_set = frozenset(roles)
            unknown_roles = role_set.difference(ROLE_PERMISSIONS)
            if unknown_roles:
                raise AuthenticationConfigurationError(
                    f"{prefix}.roles contains unknown values: {', '.join(sorted(unknown_roles))}."
                )
            if not isinstance(active, bool):
                raise AuthenticationConfigurationError(f"{prefix}.active must be boolean.")
            expires_at = _parse_datetime(entry.get("expires_at"), f"{prefix}.expires_at")
            if active:
                principals.append(
                    Principal(
                        identifier=identifier,
                        name=name.strip(),
                        roles=role_set,
                        token_digest=digest,
                        expires_at=expires_at,
                    )
                )
        return principals


class SlidingWindowRateLimiter:
    def __init__(self, requests_per_minute: int):
        self.limit = requests_per_minute
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 10_000:
                self._events = {
                    item: queue
                    for item, queue in self._events.items()
                    if queue and queue[-1] > cutoff
                }
            return True


def _source_ip(scope: Scope) -> str:
    client = scope.get("client")
    peer = client[0] if client else "unknown"
    try:
        is_loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        is_loopback = False
    headers = Headers(scope=scope)
    forwarded = headers.get("x-forwarded-for", "")
    if is_loopback and forwarded:
        candidate = forwarded.split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer[:64]


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, registry: TokenRegistry, *, requests_per_minute: int):
        self.app = app
        self.registry = registry
        self.limiter = SlidingWindowRateLimiter(requests_per_minute)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/healthz":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        principal = None
        if separator == " " and scheme.lower() == "bearer" and token.strip():
            try:
                principal = self.registry.authenticate(token.strip())
            except AuthenticationConfigurationError:
                _logger.exception("MCP token registry could not be loaded")
                await self._error(
                    scope,
                    receive,
                    send,
                    503,
                    "authentication_unavailable",
                    "Authentication is temporarily unavailable.",
                )
                return

        source_ip = _source_ip(scope)
        if principal is None:
            if not self.limiter.allow(f"invalid:{source_ip}"):
                await self._error(scope, receive, send, 429, "rate_limited", "Too many requests.")
                return
            _logger.warning("Rejected MCP authentication from source_ip=%s", source_ip)
            await self._error(
                scope,
                receive,
                send,
                401,
                "unauthorized",
                "A valid collaborator token is required.",
            )
            return
        if not self.limiter.allow(f"user:{principal.identifier}"):
            _logger.warning("Rate limit exceeded actor=%s", principal.identifier)
            await self._error(scope, receive, send, 429, "rate_limited", "Too many requests.")
            return

        scope.setdefault("state", {})["mcp_principal"] = principal
        context_token = _current_principal.set(principal)
        started = time.monotonic()
        status_code = 500

        async def send_with_security_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                mutable = MutableHeaders(scope=message)
                mutable.append("Cache-Control", "no-store")
                mutable.append("X-Content-Type-Options", "nosniff")
            await send(message)

        try:
            await self.app(scope, receive, send_with_security_headers)
        finally:
            _current_principal.reset(context_token)
            _logger.info(
                "MCP request actor=%s method=%s path=%s status=%s duration_ms=%s source_ip=%s",
                principal.identifier,
                scope.get("method"),
                path,
                status_code,
                round((time.monotonic() - started) * 1000),
                source_ip,
            )

    @staticmethod
    async def _error(
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        code: str,
        message: str,
    ) -> None:
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if status == 401:
            headers["WWW-Authenticate"] = 'Bearer realm="odoo-project-mcp"'
        if status == 429:
            headers["Retry-After"] = "60"
        response = JSONResponse({"error": {"code": code, "message": message}}, status, headers)
        await response(scope, receive, send)
