import pytest

from odoo_project_mcp.config import ConfigurationError, Settings


def base_env(monkeypatch):
    monkeypatch.setenv("ODOO_BASE_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_LOGIN", "mcp@example.test")
    monkeypatch.setenv("ODOO_API_KEY", "secret")


def test_stdio_remains_default(monkeypatch):
    base_env(monkeypatch)
    assert Settings.from_env().transport == "stdio"


def test_http_requires_public_url_and_users_file(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    with pytest.raises(ConfigurationError, match="MCP_PUBLIC_URL"):
        Settings.from_env()

    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.test/mcp")
    with pytest.raises(ConfigurationError, match="MCP_USERS_FILE"):
        Settings.from_env()

    monkeypatch.setenv("MCP_USERS_FILE", "/secure/users.json")
    settings = Settings.from_env()
    assert settings.mcp_allowed_hosts == ("mcp.example.test",)
    assert settings.mcp_allowed_origins == ("https://mcp.example.test",)


def test_http_rejects_plain_http(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://mcp.example.test/mcp")
    monkeypatch.setenv("MCP_USERS_FILE", "/secure/users.json")
    with pytest.raises(ConfigurationError, match="must use HTTPS"):
        Settings.from_env()


def test_http_rejects_public_bind_by_default(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.test/mcp")
    monkeypatch.setenv("MCP_USERS_FILE", "/secure/users.json")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    with pytest.raises(ConfigurationError, match="must be loopback"):
        Settings.from_env()
