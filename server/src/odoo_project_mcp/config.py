import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


class ConfigurationError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable {name} is not set.")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _csv(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    login: str
    database: str | None
    agent_name: str
    timeout_seconds: float
    verify_tls: bool
    transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_path: str = "/mcp"
    mcp_public_url: str | None = None
    mcp_allowed_hosts: tuple[str, ...] = ()
    mcp_allowed_origins: tuple[str, ...] = ()
    mcp_users_file: Path | None = None
    mcp_rate_limit_per_minute: int = 120
    mcp_strict_file_permissions: bool = True
    mcp_forwarded_allow_ips: str = "127.0.0.1,::1"

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = _required("ODOO_BASE_URL").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("ODOO_BASE_URL must be an absolute http(s) URL.")
        allow_http = _boolean("ODOO_ALLOW_HTTP", False)
        is_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (allow_http or is_local):
            raise ConfigurationError(
                "ODOO_BASE_URL must use HTTPS. Set ODOO_ALLOW_HTTP=true only for "
                "trusted development networks."
            )

        try:
            timeout = float(os.getenv("ODOO_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise ConfigurationError("ODOO_TIMEOUT_SECONDS must be numeric.") from exc
        if not 1 <= timeout <= 120:
            raise ConfigurationError("ODOO_TIMEOUT_SECONDS must be between 1 and 120.")

        agent_name = os.getenv("ODOO_MCP_AGENT", "claude-code").strip()
        if not agent_name or len(agent_name) > 128:
            raise ConfigurationError("ODOO_MCP_AGENT must contain 1-128 characters.")

        transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
        if transport == "http":
            transport = "streamable-http"
        if transport not in {"stdio", "streamable-http"}:
            raise ConfigurationError("MCP_TRANSPORT must be stdio or streamable-http.")

        mcp_host = os.getenv("MCP_HOST", "127.0.0.1").strip()
        if not mcp_host or any(character.isspace() for character in mcp_host):
            raise ConfigurationError("MCP_HOST is invalid.")
        mcp_port = _integer("MCP_PORT", 8000, 1, 65535)
        mcp_path = os.getenv("MCP_PATH", "/mcp").strip()
        if not mcp_path.startswith("/") or "?" in mcp_path or "#" in mcp_path:
            raise ConfigurationError("MCP_PATH must be an absolute URL path.")

        public_url = os.getenv("MCP_PUBLIC_URL", "").strip() or None
        users_file_raw = os.getenv("MCP_USERS_FILE", "").strip()
        allowed_hosts = _csv("MCP_ALLOWED_HOSTS")
        allowed_origins = _csv("MCP_ALLOWED_ORIGINS")
        if transport == "streamable-http":
            if not public_url:
                raise ConfigurationError("MCP_PUBLIC_URL is required for streamable-http.")
            public_parsed = urlparse(public_url)
            allow_insecure = _boolean("MCP_ALLOW_INSECURE_HTTP", False)
            if (
                public_parsed.scheme not in {"http", "https"}
                or not public_parsed.netloc
                or public_parsed.query
                or public_parsed.fragment
            ):
                raise ConfigurationError("MCP_PUBLIC_URL must be an absolute HTTP(S) URL.")
            if public_parsed.scheme != "https" and not allow_insecure:
                raise ConfigurationError(
                    "MCP_PUBLIC_URL must use HTTPS. Set MCP_ALLOW_INSECURE_HTTP=true "
                    "only for local testing."
                )
            if public_parsed.path.rstrip("/") != mcp_path.rstrip("/"):
                raise ConfigurationError("MCP_PUBLIC_URL path must match MCP_PATH.")
            if not users_file_raw:
                raise ConfigurationError("MCP_USERS_FILE is required for streamable-http.")
            is_loopback_bind = mcp_host in {"127.0.0.1", "localhost", "::1"}
            if not is_loopback_bind and not _boolean("MCP_ALLOW_PUBLIC_BIND", False):
                raise ConfigurationError(
                    "MCP_HOST must be loopback behind a reverse proxy. Set "
                    "MCP_ALLOW_PUBLIC_BIND=true only for an isolated container network."
                )
            if not allowed_hosts:
                allowed_hosts = (public_parsed.netloc,)
            if not allowed_origins:
                allowed_origins = (f"{public_parsed.scheme}://{public_parsed.netloc}",)

        return cls(
            base_url=base_url,
            api_key=_required("ODOO_API_KEY"),
            login=_required("ODOO_LOGIN"),
            database=os.getenv("ODOO_DATABASE", "").strip() or None,
            agent_name=agent_name,
            timeout_seconds=timeout,
            verify_tls=_boolean("ODOO_VERIFY_TLS", True),
            transport=transport,
            mcp_host=mcp_host,
            mcp_port=mcp_port,
            mcp_path=mcp_path,
            mcp_public_url=public_url,
            mcp_allowed_hosts=allowed_hosts,
            mcp_allowed_origins=allowed_origins,
            mcp_users_file=Path(users_file_raw) if users_file_raw else None,
            mcp_rate_limit_per_minute=_integer("MCP_RATE_LIMIT_PER_MINUTE", 120, 1, 10000),
            mcp_strict_file_permissions=_boolean("MCP_STRICT_FILE_PERMISSIONS", True),
            mcp_forwarded_allow_ips=os.getenv(
                "MCP_FORWARDED_ALLOW_IPS", "127.0.0.1,::1"
            ).strip(),
        )
