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
                "User-Agent": "odoo-project-mcp/0.2.0",
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

    def get_task(self, task_id: int) -> dict[str, Any]:
        return self._request("GET", f"/mcp/v1/tasks/{task_id}")

    def start_task(
        self, task_id: int, *, agent: str, git_branch: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"agent": agent}
        if git_branch:
            body["git_branch"] = git_branch
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/start", json=body)

    def add_comment(self, task_id: int, *, comment: str) -> dict[str, Any]:
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/comments", json={"comment": comment})

    def complete_task(
        self,
        task_id: int,
        *,
        summary: str,
        agent: str,
        git_branch: str | None = None,
        git_commit: str | None = None,
        mark_done: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"summary": summary, "agent": agent, "mark_done": mark_done}
        if git_branch:
            body["git_branch"] = git_branch
        if git_commit:
            body["git_commit"] = git_commit
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/complete", json=body)

    def verify_task(
        self,
        task_id: int,
        *,
        result: str,
        notes: str,
        agent: str,
        update_task_state: bool = True,
    ) -> dict[str, Any]:
        body = {
            "result": result,
            "notes": notes,
            "agent": agent,
            "update_task_state": update_task_state,
        }
        return self._request("POST", f"/mcp/v1/tasks/{task_id}/verify", json=body)
