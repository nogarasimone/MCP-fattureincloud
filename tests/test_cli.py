"""Configurazione e modalità HTTP della CLI."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from fic_mcp.__main__ import _settings, _transport_security
from fic_mcp.config import Settings
from fic_mcp.server import build_server

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}
HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.mark.parametrize("key,value,expected", [
    ("FIC_TOOLSETS", "xyz", "FIC_TOOLSETS"),
    ("FIC_PRIVACY_MODE", "boh", "FIC_PRIVACY_MODE"),
    ("FIC_COMPANY_ID", "abc", "FIC_COMPANY_ID deve essere un numero"),
    ("FIC_MAX_RETRIES", "1.5", "FIC_MAX_RETRIES deve essere un numero"),
])
def test_invalid_config_is_readable(monkeypatch, tmp_path, key, value, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as exc:
        _settings(type("Args", (), {"env_file": None})())
    assert str(exc.value).startswith("Configurazione non valida:") and expected in str(exc.value)


def _status(settings: Settings, bind: str, **headers) -> int:
    server, _ = build_server(settings)
    app = server.streamable_http_app(host=bind, transport_security=_transport_security(settings))
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        return client.post("/mcp", json=INIT, headers={**HEADERS, **headers}).status_code


def test_public_host_rejected_without_allowlist(tmp_path):
    settings = Settings(access_token="t", audit_log=None, token_file=tmp_path / "t.json")
    assert _status(settings, "127.0.0.1") == 200
    assert _status(settings, "127.0.0.1", host="mcp.example.it") == 421


def test_allowed_hosts_behind_proxy(tmp_path):
    settings = Settings(access_token="t", audit_log=None, token_file=tmp_path / "t.json",
                        allowed_hosts=("mcp.example.it",))
    assert _status(settings, "127.0.0.1", host="mcp.example.it") == 200
    assert _status(settings, "127.0.0.1", host="mcp.example.it:443", origin="https://mcp.example.it") == 200
    assert _status(settings, "127.0.0.1", host="evil.example") == 421
    assert _status(settings, "127.0.0.1", host="mcp.example.it", origin="https://evil.example") == 403
