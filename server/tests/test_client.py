import json

import httpx
import pytest

from odoo_project_mcp.client import OdooAPIError, OdooProjectClient
from odoo_project_mcp.config import Settings


def settings(database: str | None = None) -> Settings:
    return Settings(
        base_url="https://odoo.example.test",
        api_key="secret-test-key",
        login="claude.mcp@example.test",
        database=database,
        agent_name="claude-code",
        timeout_seconds=5,
        verify_tls=True,
    )


def test_list_projects_sends_authentication_and_filters():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/web":
            assert request.url.params["db"] == "odoo17"
            assert "authorization" not in request.headers
            return httpx.Response(
                302,
                headers={"Location": str(request.url), "Set-Cookie": "session_id=test; Path=/"},
            )
        assert request.headers["authorization"] == "Bearer secret-test-key"
        assert request.headers["x-odoo-login"] == "claude.mcp@example.test"
        assert request.url.params["db"] == "odoo17"
        assert request.url.params["name"] == "Farmacia"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "request_id": request.headers["x-request-id"],
                "data": {"items": [{"id": 8, "name": "Farmacia"}], "total": 1},
            },
        )

    client = OdooProjectClient(settings(database="odoo17"), transport=httpx.MockTransport(handler))
    result = client.list_projects(name="Farmacia")
    assert result["items"][0]["id"] == 8
    client.close()


def test_complete_task_sends_only_the_allowed_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/v1/tasks/42/complete"
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "summary": "Implemented and tested",
            "agent": "claude-code",
            "mark_done": True,
            "git_branch": "feat/lots",
            "git_commit": "abcdef1234567",
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "request_id": request.headers["x-request-id"],
                "data": {"id": 42, "ai_status": "completed"},
            },
        )

    client = OdooProjectClient(settings(), transport=httpx.MockTransport(handler))
    result = client.complete_task(
        42,
        summary="Implemented and tested",
        agent="claude-code",
        git_branch="feat/lots",
        git_commit="abcdef1234567",
    )
    assert result["ai_status"] == "completed"
    client.close()


def test_api_error_is_sanitized_and_typed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "ok": False,
                "request_id": "test",
                "error": {"code": "forbidden", "message": "Access denied."},
            },
        )

    client = OdooProjectClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(OdooAPIError) as error:
        client.get_task(99)
    assert error.value.status_code == 403
    assert error.value.code == "forbidden"
    assert str(error.value) == "Access denied."
    client.close()


def test_redirect_is_not_followed():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(303, headers={"Location": "/web/login"})

    client = OdooProjectClient(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(OdooAPIError) as error:
        client.get_task(1)
    assert error.value.code == "unexpected_redirect"
    client.close()
