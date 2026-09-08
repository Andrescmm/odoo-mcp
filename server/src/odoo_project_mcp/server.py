import atexit
import logging
from typing import Literal

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import BearerTokenMiddleware, TokenRegistry, current_principal
from .client import OdooProjectClient
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = Settings.from_env()
client = OdooProjectClient(settings)
atexit.register(client.close)

mcp = FastMCP(
    "Odoo Project",
    instructions=(
        "Use these tools only for Project tasks visible to the configured Odoo service user. "
        "Read the task before changing it. Mutating tools write an audit record in Odoo."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
    streamable_http_path=settings.mcp_path,
    json_response=True,
    stateless_http=True,
    max_request_body_size=1024 * 1024,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.mcp_allowed_hosts)
        if settings.mcp_allowed_hosts
        else ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=list(settings.mcp_allowed_origins)
        if settings.mcp_allowed_origins
        else ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    ),
)


@mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "odoo-project-mcp"})


def _positive_id(value: int, field: str) -> int:
    if value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _page(limit: int, offset: int) -> tuple[int, int]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    if offset < 0:
        raise ValueError("offset must be non-negative.")
    return limit, offset


def _authorize(permission: str):
    if settings.transport == "stdio":
        return None
    principal = current_principal()
    if principal is None:
        raise PermissionError("An authenticated collaborator is required.")
    if not principal.can(permission):
        raise PermissionError(
            f"Collaborator {principal.identifier} does not have permission {permission}."
        )
    return principal


def _agent(explicit: str | None, permission: str) -> str:
    principal = _authorize(permission)
    if principal:
        return f"{settings.agent_name}:{principal.identifier}"[:128]
    return explicit or settings.agent_name


@mcp.tool()
def list_projects(
    name: str | None = None,
    active: Literal["true", "false", "all"] = "true",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List Odoo projects visible to the configured user, optionally filtering by name."""
    _authorize("projects:read")
    limit, offset = _page(limit, offset)
    return client.list_projects(name=name, active=active, limit=limit, offset=offset)


@mcp.tool()
def list_tasks(
    project_id: int,
    ai_status: Literal["pending", "in_progress", "completed", "changes_requested", "verified"]
    | None = None,
    task_state: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List visible tasks in one Odoo project, with optional AI and native task-state filters."""
    _authorize("tasks:read")
    limit, offset = _page(limit, offset)
    return client.list_tasks(
        _positive_id(project_id, "project_id"),
        ai_status=ai_status,
        task_state=task_state,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def get_task(task_id: int) -> dict:
    """Read one visible Odoo task with its description and implementation metadata."""
    _authorize("tasks:read")
    return client.get_task(_positive_id(task_id, "task_id"))


@mcp.tool()
def start_task(task_id: int, git_branch: str | None = None, agent: str | None = None) -> dict:
    """Mark an Odoo task as being worked by the AI agent and set its native state to In Progress."""
    return client.start_task(
        _positive_id(task_id, "task_id"),
        agent=_agent(agent, "tasks:write"),
        git_branch=git_branch,
    )


@mcp.tool()
def add_comment(task_id: int, comment: str) -> dict:
    """Add an internal-note comment to an Odoo task through the restricted API."""
    if not comment.strip():
        raise ValueError("comment cannot be empty.")
    _authorize("tasks:comment")
    return client.add_comment(_positive_id(task_id, "task_id"), comment=comment)


@mcp.tool()
def complete_task(
    task_id: int,
    summary: str,
    git_branch: str | None = None,
    git_commit: str | None = None,
    mark_done: bool = True,
    agent: str | None = None,
) -> dict:
    """Record implementation results and optionally mark the native Odoo task state Done."""
    if not summary.strip():
        raise ValueError("summary cannot be empty.")
    return client.complete_task(
        _positive_id(task_id, "task_id"),
        summary=summary,
        agent=_agent(agent, "tasks:write"),
        git_branch=git_branch,
        git_commit=git_commit,
        mark_done=mark_done,
    )


@mcp.tool()
def verify_task(
    task_id: int,
    result: Literal["passed", "failed"],
    notes: str,
    update_task_state: bool = True,
    agent: str | None = None,
) -> dict:
    """Record verification and optionally update the native Odoo task state."""
    if not notes.strip():
        raise ValueError("notes cannot be empty.")
    return client.verify_task(
        _positive_id(task_id, "task_id"),
        result=result,
        notes=notes,
        agent=_agent(agent, "tasks:verify"),
        update_task_state=update_task_state,
    )


def main() -> None:
    if settings.transport == "stdio":
        mcp.run(transport="stdio")
        return
    if settings.mcp_users_file is None:
        raise RuntimeError("MCP_USERS_FILE is required for streamable-http.")
    registry = TokenRegistry(
        settings.mcp_users_file,
        strict_permissions=settings.mcp_strict_file_permissions,
    )
    registry.validate()
    app = BearerTokenMiddleware(
        mcp.streamable_http_app(),
        registry,
        requests_per_minute=settings.mcp_rate_limit_per_minute,
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.mcp_forwarded_allow_ips,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
