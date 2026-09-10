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


def _optional(**values):
    return {key: value for key, value in values.items() if value is not None}


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
    ai_status: Literal[
        "pending", "in_progress", "completed", "changes_requested", "verified", "cancelled"
    ]
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
def search_tasks(
    name: str | None = None,
    project_id: int | None = None,
    stage_id: int | None = None,
    assignee_id: int | None = None,
    parent_id: int | None = None,
    ai_status: Literal[
        "pending", "in_progress", "completed", "changes_requested", "verified", "cancelled"
    ]
    | None = None,
    task_state: str | None = None,
    active: Literal["true", "false", "all"] = "true",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search visible tasks across projects using only the supported filters."""
    _authorize("tasks:read")
    limit, offset = _page(limit, offset)
    for value, field in (
        (project_id, "project_id"),
        (stage_id, "stage_id"),
        (assignee_id, "assignee_id"),
        (parent_id, "parent_id"),
    ):
        if value is not None:
            _positive_id(value, field)
    return client.search_tasks(
        name=name,
        project_id=project_id,
        stage_id=stage_id,
        assignee_id=assignee_id,
        parent_id=parent_id,
        ai_status=ai_status,
        task_state=task_state,
        active=active,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def get_task(task_id: int, include_archived: bool = False) -> dict:
    """Read one visible Odoo task with its description and implementation metadata."""
    _authorize("tasks:read")
    return client.get_task(
        _positive_id(task_id, "task_id"), include_archived=include_archived
    )


@mcp.tool()
def list_project_stages(project_id: int) -> dict:
    """List the valid workflow stages for one visible project."""
    _authorize("tasks:read")
    return client.list_project_stages(_positive_id(project_id, "project_id"))


@mcp.tool()
def list_project_members(project_id: int) -> dict:
    """List visible internal members associated with a project or its tasks."""
    _authorize("tasks:read")
    return client.list_project_members(_positive_id(project_id, "project_id"))


@mcp.tool()
def list_task_comments(task_id: int, limit: int = 50, offset: int = 0) -> dict:
    """List recent chatter comments and internal notes for a visible task."""
    _authorize("tasks:read")
    limit, offset = _page(limit, offset)
    return client.list_task_comments(
        _positive_id(task_id, "task_id"), limit=limit, offset=offset
    )


@mcp.tool()
def create_task(
    project_id: int,
    name: str,
    description: str | None = None,
    priority: Literal["0", "1"] | None = None,
    deadline: str | None = None,
    allocated_hours: float | None = None,
    tags: list[str] | None = None,
    stage_id: int | None = None,
    stage_name: str | None = None,
    assignee_ids: list[int] | None = None,
    assignee_logins: list[str] | None = None,
    acceptance_criteria: str | None = None,
    git_repository: str | None = None,
    agent: str | None = None,
) -> dict:
    """Create a task in a visible project using an explicit allowlist of fields."""
    if not name.strip():
        raise ValueError("name cannot be empty.")
    values = _optional(
        project_id=_positive_id(project_id, "project_id"),
        name=name,
        description=description,
        priority=priority,
        deadline=deadline,
        allocated_hours=allocated_hours,
        tags=tags,
        stage_id=stage_id,
        stage_name=stage_name,
        assignee_ids=assignee_ids,
        assignee_logins=assignee_logins,
        acceptance_criteria=acceptance_criteria,
        git_repository=git_repository,
        agent=_agent(agent, "tasks:write"),
    )
    return client.create_task(**values)


@mcp.tool()
def create_subtask(
    parent_task_id: int,
    name: str,
    description: str | None = None,
    priority: Literal["0", "1"] | None = None,
    deadline: str | None = None,
    allocated_hours: float | None = None,
    tags: list[str] | None = None,
    assignee_ids: list[int] | None = None,
    assignee_logins: list[str] | None = None,
    acceptance_criteria: str | None = None,
    agent: str | None = None,
) -> dict:
    """Create a child task under a visible parent task in the same project."""
    if not name.strip():
        raise ValueError("name cannot be empty.")
    parent = client.get_task(_positive_id(parent_task_id, "parent_task_id"))
    project_id = parent.get("project_id")
    if not project_id:
        raise ValueError("The parent task has no project.")
    values = _optional(
        project_id=project_id,
        parent_id=parent_task_id,
        name=name,
        description=description,
        priority=priority,
        deadline=deadline,
        allocated_hours=allocated_hours,
        tags=tags,
        assignee_ids=assignee_ids,
        assignee_logins=assignee_logins,
        acceptance_criteria=acceptance_criteria,
        agent=_agent(agent, "tasks:write"),
    )
    return client.create_task(**values)


@mcp.tool()
def update_task(
    task_id: int,
    name: str | None = None,
    description: str | None = None,
    priority: Literal["0", "1"] | None = None,
    deadline: str | None = None,
    allocated_hours: float | None = None,
    tags: list[str] | None = None,
    acceptance_criteria: str | None = None,
    test_results: str | None = None,
    git_repository: str | None = None,
    pull_request_url: str | None = None,
    deployment_environment: str | None = None,
    verification_evidence: str | None = None,
    ai_blockers: str | None = None,
    note: str | None = None,
    expected_write_date: str | None = None,
) -> dict:
    """Update supported task fields; expected_write_date protects against stale writes."""
    _authorize("tasks:write")
    values = _optional(
        name=name,
        description=description,
        priority=priority,
        deadline=deadline,
        allocated_hours=allocated_hours,
        tags=tags,
        acceptance_criteria=acceptance_criteria,
        test_results=test_results,
        git_repository=git_repository,
        pull_request_url=pull_request_url,
        deployment_environment=deployment_environment,
        verification_evidence=verification_evidence,
        ai_blockers=ai_blockers,
        note=note,
        expected_write_date=expected_write_date,
    )
    if not any(key not in {"note", "expected_write_date"} for key in values):
        raise ValueError("At least one task field must be provided.")
    return client.update_task(_positive_id(task_id, "task_id"), **values)


@mcp.tool()
def start_task(
    task_id: int,
    git_branch: str | None = None,
    git_repository: str | None = None,
    expected_write_date: str | None = None,
    agent: str | None = None,
) -> dict:
    """Mark an Odoo task as being worked by the AI agent and set its native state to In Progress."""
    return client.start_task(
        _positive_id(task_id, "task_id"),
        agent=_agent(agent, "tasks:write"),
        git_branch=git_branch,
        git_repository=git_repository,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def add_comment(
    task_id: int, comment: str, expected_write_date: str | None = None
) -> dict:
    """Add an internal-note comment to an Odoo task through the restricted API."""
    if not comment.strip():
        raise ValueError("comment cannot be empty.")
    _authorize("tasks:comment")
    return client.add_comment(
        _positive_id(task_id, "task_id"),
        comment=comment,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def complete_task(
    task_id: int,
    summary: str,
    git_branch: str | None = None,
    git_commit: str | None = None,
    test_results: str | None = None,
    pull_request_url: str | None = None,
    deployment_environment: str | None = None,
    mark_done: bool = True,
    expected_write_date: str | None = None,
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
        test_results=test_results,
        pull_request_url=pull_request_url,
        deployment_environment=deployment_environment,
        mark_done=mark_done,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def verify_task(
    task_id: int,
    result: Literal["passed", "failed"],
    notes: str,
    evidence: str | None = None,
    update_task_state: bool = True,
    expected_write_date: str | None = None,
    agent: str | None = None,
) -> dict:
    """Record verification and optionally update the native Odoo task state."""
    if not notes.strip():
        raise ValueError("notes cannot be empty.")
    return client.verify_task(
        _positive_id(task_id, "task_id"),
        result=result,
        notes=notes,
        evidence=evidence,
        agent=_agent(agent, "tasks:verify"),
        update_task_state=update_task_state,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def set_task_stage(
    task_id: int,
    stage_id: int | None = None,
    stage_name: str | None = None,
    note: str | None = None,
    expected_write_date: str | None = None,
) -> dict:
    """Move a task to a stage available in its own project."""
    _authorize("tasks:write")
    if stage_id is None and not (stage_name and stage_name.strip()):
        raise ValueError("stage_id or stage_name is required.")
    if stage_id is not None:
        _positive_id(stage_id, "stage_id")
    return client.set_task_stage(
        _positive_id(task_id, "task_id"),
        stage_id=stage_id,
        stage_name=stage_name,
        note=note,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def set_task_assignees(
    task_id: int,
    mode: Literal["set", "add", "remove"] = "set",
    assignee_ids: list[int] | None = None,
    assignee_logins: list[str] | None = None,
    note: str | None = None,
    expected_write_date: str | None = None,
) -> dict:
    """Replace, add or remove task assignees by visible Odoo id or exact login."""
    _authorize("tasks:write")
    for user_id in assignee_ids or []:
        _positive_id(user_id, "assignee_id")
    return client.set_task_assignees(
        _positive_id(task_id, "task_id"),
        mode=mode,
        assignee_ids=assignee_ids,
        assignee_logins=assignee_logins,
        note=note,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def set_task_dependencies(
    task_id: int,
    dependency_ids: list[int],
    mode: Literal["set", "add", "remove"] = "set",
    note: str | None = None,
    expected_write_date: str | None = None,
) -> dict:
    """Replace, add or remove same-project tasks that block this task."""
    _authorize("tasks:write")
    for dependency_id in dependency_ids:
        _positive_id(dependency_id, "dependency_id")
    return client.set_task_dependencies(
        _positive_id(task_id, "task_id"),
        mode=mode,
        dependency_ids=dependency_ids,
        note=note,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def reopen_task(
    task_id: int,
    note: str | None = None,
    expected_write_date: str | None = None,
) -> dict:
    """Reopen a task by setting its native Odoo state to In Progress."""
    _authorize("tasks:write")
    return client.set_task_state(
        _positive_id(task_id, "task_id"),
        state="01_in_progress",
        note=note,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def cancel_task(
    task_id: int,
    note: str,
    expected_write_date: str | None = None,
) -> dict:
    """Cancel a task without deleting it; a reason is required and audited."""
    if not note.strip():
        raise ValueError("note cannot be empty.")
    _authorize("tasks:write")
    return client.set_task_state(
        _positive_id(task_id, "task_id"),
        state="1_canceled",
        note=note,
        expected_write_date=expected_write_date,
    )


@mcp.tool()
def log_work(
    task_id: int,
    description: str,
    hours: float,
    date: str | None = None,
    expected_write_date: str | None = None,
) -> dict:
    """Log hours on a task when Odoo Timesheets is installed and enabled."""
    if not description.strip():
        raise ValueError("description cannot be empty.")
    if not 0.01 <= hours <= 24:
        raise ValueError("hours must be between 0.01 and 24.")
    _authorize("tasks:write")
    return client.log_work(
        _positive_id(task_id, "task_id"),
        description=description,
        hours=hours,
        date=date,
        expected_write_date=expected_write_date,
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
