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


def test_create_subtask_contract_and_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/v1/tasks"
        assert json.loads(request.content) == {
            "project_id": 8,
            "parent_id": 42,
            "name": "Add regression tests",
            "acceptance_criteria": "Covers the reported bug",
            "agent": "claude-code:developer@example.test",
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "request_id": request.headers["x-request-id"],
                "data": {"id": 43, "parent_id": 42},
            },
        )

    client = OdooProjectClient(settings(), transport=httpx.MockTransport(handler))
    result = client.create_task(
        project_id=8,
        parent_id=42,
        name="Add regression tests",
        acceptance_criteria="Covers the reported bug",
        agent="claude-code:developer@example.test",
    )
    assert result == {"id": 43, "parent_id": 42}
    client.close()


def test_update_task_sends_expected_write_date():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/v1/tasks/42/update"
        assert json.loads(request.content) == {
            "pull_request_url": "https://github.com/example/repo/pull/7",
            "test_results": "12 tests passed",
            "expected_write_date": "2026-09-10 10:30:00",
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "request_id": request.headers["x-request-id"],
                "data": {"id": 42},
            },
        )

    client = OdooProjectClient(settings(), transport=httpx.MockTransport(handler))
    result = client.update_task(
        42,
        pull_request_url="https://github.com/example/repo/pull/7",
        test_results="12 tests passed",
        expected_write_date="2026-09-10 10:30:00",
    )
    assert result["id"] == 42
    client.close()


def test_dependencies_and_worklog_contracts():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "request_id": request.headers["x-request-id"],
                "data": {"task_id": 42},
            },
        )

    client = OdooProjectClient(settings(), transport=httpx.MockTransport(handler))
    client.set_task_dependencies(42, mode="set", dependency_ids=[10, 11])
    client.log_work(42, description="Implemented endpoint", hours=1.5, date="2026-09-10")
    assert seen == [
        (
            "/mcp/v1/tasks/42/dependencies",
            {"mode": "set", "dependency_ids": [10, 11]},
        ),
        (
            "/mcp/v1/tasks/42/worklogs",
            {"description": "Implemented endpoint", "hours": 1.5, "date": "2026-09-10"},
        ),
    ]
    client.close()
