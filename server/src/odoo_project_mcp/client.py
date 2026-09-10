import logging
import threading
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from .auth import current_principal
from .config import Settings

_logger = logging.getLogger(__name__)


class OdooAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class OdooProjectClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._database_session_ready = not settings.database
        self._database_session_lock = threading.Lock()
        self._client = httpx.Client(
            base_url=settings.base_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "odoo-project-mcp/0.3.0",
            },
            timeout=settings.timeout_seconds,
            verify=settings.verify_tls,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _ensure_database_session(self) -> None:
        """Select a database for Odoo's HTTP router without authenticating a web session."""
        if self._database_session_ready:
            return
        with self._database_session_lock:
            if self._database_session_ready:
                return
            try:
                response = self._client.get(
                    "/web",
                    params={"db": self.settings.database},
                    headers={"Accept": "text/html"},
                )
            except httpx.RequestError as exc:
                raise OdooAPIError(f"Could not select the Odoo database: {exc}") from exc
            location = response.headers.get("location", "")
            if response.status_code >= 400 or "/web/database/" in location:
                raise OdooAPIError(
                    "Odoo could not select ODOO_DATABASE. Check dbfilter, list_db, "
                    "and the database name.",
                    status_code=response.status_code,
                    code="database_selection_failed",
                )
            self._database_session_ready = True

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self._ensure_database_session()
        request_id = str(uuid.uuid4())
        params = dict(kwargs.pop("params", {}) or {})
        if self.settings.database:
            params["db"] = self.settings.database
        request_headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "X-Odoo-Login": self.settings.login,
            "X-Request-ID": request_id,
        }
        principal = current_principal()
        if principal:
            request_headers["X-MCP-Actor-ID"] = principal.identifier
            request_headers["X-MCP-Actor-Name"] = quote(principal.name, safe="")
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                headers=request_headers,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise OdooAPIError("Odoo did not respond before the configured timeout.") from exc
        except httpx.RequestError as exc:
            raise OdooAPIError(f"Could not connect to Odoo: {exc}") from exc

        if response.is_redirect:
            location = response.headers.get("location", "")
            raise OdooAPIError(
                "Odoo returned a redirect instead of API JSON "
                f"(location: {location or 'unknown'}). "
                "Check ODOO_BASE_URL, ODOO_DATABASE, dbfilter, and HTTPS proxy settings.",
                status_code=response.status_code,
                code="unexpected_redirect",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OdooAPIError(
                f"Odoo returned a non-JSON response (HTTP {response.status_code}).",
                status_code=response.status_code,
                code="invalid_response",
            ) from exc

        response_request_id = payload.get("request_id") if isinstance(payload, dict) else None
        if response_request_id and response_request_id != request_id:
            _logger.warning(
                "Odoo response request ID differs from the sent ID: sent=%s received=%s",
                request_id,
                response_request_id,
            )

        if not response.is_success or not isinstance(payload, dict) or not payload.get("ok"):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code", "http_error")
            message = error.get(
                "message", f"Odoo API request failed with HTTP {response.status_code}."
            )
            raise OdooAPIError(message, status_code=response.status_code, code=code)
        return payload.get("data")

    def list_projects(
        self, *, name: str | None = None, active: str = "true", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"active": active, "limit": limit, "offset": offset}
        if name:
            params["name"] = name
        return self._request("GET", "/mcp/v1/projects", params=params)

    def list_tasks(
        self,
        project_id: int,
        *,
        ai_status: str | None = None,
        task_state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if ai_status:
            params["ai_status"] = ai_status
        if task_state:
            params["task_state"] = task_state
        return self._request("GET", f"/mcp/v1/projects/{project_id}/tasks", params=params)

    def search_tasks(
        self,
        *,
        name: str | None = None,
        project_id: int | None = None,
        stage_id: int | None = None,
        assignee_id: int | None = None,
        parent_id: int | None = None,
        ai_status: str | None = None,
        task_state: str | None = None,
        active: str = "true",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"active": active, "limit": limit, "offset": offset}
        for key, value in (
            ("name", name),
            ("project_id", project_id),
            ("stage_id", stage_id),
            ("assignee_id", assignee_id),
            ("parent_id", parent_id),
            ("ai_status", ai_status),
            ("task_state", task_state),
        ):
            if value is not None:
                params[key] = value
        return self._request("GET", "/mcp/v1/tasks", params=params)

    def get_task(self, task_id: int, *, include_archived: bool = False) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/mcp/v1/tasks/{task_id}",
            params={"include_archived": str(include_archived).lower()},
        )

    def list_project_stages(self, project_id: int) -> dict[str, Any]:
        return self._request("GET", f"/mcp/v1/projects/{project_id}/stages")

    def list_project_members(self, project_id: int) -> dict[str, Any]:
        return self._request("GET", f"/mcp/v1/projects/{project_id}/members")

    def list_task_comments(
        self, task_id: int, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/mcp/v1/tasks/{task_id}/comments",
            params={"limit": limit, "offset": offset},
        )

    def create_task(self, **values: Any) -> dict[str, Any]:
        return self._request("POST", "/mcp/v1/tasks", json=values)

    def update_task(self, task_id: int, **values: Any) -> dict[str, Any]:
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/update", json=values)

    def start_task(
        self,
        task_id: int,
        *,
        agent: str,
        git_branch: str | None = None,
        git_repository: str | None = None,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"agent": agent}
        if git_branch:
            body["git_branch"] = git_branch
        if git_repository:
            body["git_repository"] = git_repository
        if expected_write_date:
            body["expected_write_date"] = expected_write_date
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/start", json=body)

    def add_comment(
        self, task_id: int, *, comment: str, expected_write_date: str | None = None
    ) -> dict[str, Any]:
        body = {"comment": comment}
        if expected_write_date:
            body["expected_write_date"] = expected_write_date
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/comments", json=body)

    def complete_task(
        self,
        task_id: int,
        *,
        summary: str,
        agent: str,
        git_branch: str | None = None,
        git_commit: str | None = None,
        test_results: str | None = None,
        pull_request_url: str | None = None,
        deployment_environment: str | None = None,
        mark_done: bool = True,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"summary": summary, "agent": agent, "mark_done": mark_done}
        if git_branch:
            body["git_branch"] = git_branch
        if git_commit:
            body["git_commit"] = git_commit
        if test_results:
            body["test_results"] = test_results
        if pull_request_url:
            body["pull_request_url"] = pull_request_url
        if deployment_environment:
            body["deployment_environment"] = deployment_environment
        if expected_write_date:
            body["expected_write_date"] = expected_write_date
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/complete", json=body)

    def verify_task(
        self,
        task_id: int,
        *,
        result: str,
        notes: str,
        agent: str,
        evidence: str | None = None,
        update_task_state: bool = True,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "result": result,
            "notes": notes,
            "agent": agent,
            "update_task_state": update_task_state,
        }
        if evidence:
            body["evidence"] = evidence
        if expected_write_date:
            body["expected_write_date"] = expected_write_date
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/verify", json=body)

    def set_task_stage(
        self,
        task_id: int,
        *,
        stage_id: int | None = None,
        stage_name: str | None = None,
        note: str | None = None,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body = self._optional_body(
            stage_id=stage_id,
            stage_name=stage_name,
            note=note,
            expected_write_date=expected_write_date,
        )
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/stage", json=body)

    def set_task_state(
        self,
        task_id: int,
        *,
        state: str,
        note: str | None = None,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body = self._optional_body(
            state=state, note=note, expected_write_date=expected_write_date
        )
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/state", json=body)

    def set_task_assignees(
        self,
        task_id: int,
        *,
        mode: str,
        assignee_ids: list[int] | None = None,
        assignee_logins: list[str] | None = None,
        note: str | None = None,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body = self._optional_body(
            mode=mode,
            assignee_ids=assignee_ids,
            assignee_logins=assignee_logins,
            note=note,
            expected_write_date=expected_write_date,
        )
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/assignees", json=body)

    def set_task_dependencies(
        self,
        task_id: int,
        *,
        mode: str,
        dependency_ids: list[int],
        note: str | None = None,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body = self._optional_body(
            mode=mode,
            dependency_ids=dependency_ids,
            note=note,
            expected_write_date=expected_write_date,
        )
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/dependencies", json=body)

    def log_work(
        self,
        task_id: int,
        *,
        description: str,
        hours: float,
        date: str | None = None,
        expected_write_date: str | None = None,
    ) -> dict[str, Any]:
        body = self._optional_body(
            description=description,
            hours=hours,
            date=date,
            expected_write_date=expected_write_date,
        )
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/worklogs", json=body)

    @staticmethod
    def _optional_body(**values: Any) -> dict[str, Any]:
        return {key: value for key, value in values.items() if value is not None}
