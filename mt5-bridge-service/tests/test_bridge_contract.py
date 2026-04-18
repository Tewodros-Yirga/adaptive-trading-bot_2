import os

from fastapi.testclient import TestClient

os.environ.setdefault("MT_LOGIN", "123456")
os.environ.setdefault("MT_PASSWORD", "secret")
os.environ.setdefault("MT_SERVER", "Broker-Demo")
os.environ.setdefault("MT_BRIDGE_SECRET", "bridge_secret_token")

from app.main import app  # noqa: E402


def test_health_endpoint():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_secret_protected_endpoint():
    client = TestClient(app, raise_server_exceptions=False)
    denied = client.get("/account")
    assert denied.status_code == 403
    allowed = client.get("/account", headers={"X-Bridge-Secret": "bridge_secret_token"})
    assert allowed.status_code == 500


def test_parse_ipc_probe_stdout():
    from app.main import _parse_ipc_probe_stdout

    parsed = _parse_ipc_probe_stdout("ok=False err=(-10005, 'IPC timeout')")
    assert parsed["ok"] is False
    assert parsed["err_code"] == -10005
    assert parsed["err_message"] == "IPC timeout"


def test_ready_reports_ipc_fields(monkeypatch, tmp_path):
    from app import main

    (tmp_path / "mt5_context.status").write_text("mode=portable", encoding="utf-8")
    (tmp_path / "mt5_ipc.status").write_text("ready", encoding="utf-8")
    monkeypatch.setenv("LOGDIR", str(tmp_path))
    monkeypatch.setattr(main.adapter, "account", lambda: {"backend": "mt5linux"})
    monkeypatch.setattr(main.adapter, "last_error_class", "ipc_timeout")

    client = TestClient(app)
    res = client.get("/ready")
    assert res.status_code == 200
    data = res.json()
    assert data["ready"] is False
    assert data["error"] == "mt5 ipc not ready"
    assert data["ipc_ready"] is False
    assert data["error_class"] == "ipc_timeout"
    assert "mode=portable" in (data["context_status"] or "")


def test_debug_mt5_includes_ipc_diagnostics(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setenv("LOGDIR", str(tmp_path))
    (tmp_path / "bootstrap.ready").write_text("", encoding="utf-8")
    (tmp_path / "mt5_terminal.ready").write_text("", encoding="utf-8")
    (tmp_path / "mt5_ipc.failed").write_text("", encoding="utf-8")
    (tmp_path / "mt5_ipc.status").write_text("failed: attempts_exhausted", encoding="utf-8")
    (tmp_path / "mt5_context.status").write_text("mode=portable; args=/portable", encoding="utf-8")
    (tmp_path / "mt5-ipc-probe.log").write_text("[attempt 1] ...", encoding="utf-8")

    client = TestClient(app)
    res = client.get("/debug/mt5", headers={"X-Bridge-Secret": "bridge_secret_token"})
    assert res.status_code == 200
    data = res.json()
    assert "bootstrap" in data
    assert data["bootstrap"]["ipc_ready"] is False
    assert data["bootstrap"]["ipc_failed"] is True
    assert "attempts_exhausted" in (data["bootstrap"]["ipc_status"] or "")
    assert "mode=portable" in (data["bootstrap"]["context_status"] or "")
    assert data["bootstrap"]["mt5_ipc_probe_log_exists"] is True
    assert data["runtime_env"]["mt_login_configured"] is True
    assert data["runtime_env"]["mt_server_configured"] is True
    assert "mt5-ipc-probe" in data["logs"]


def test_debug_mt5_probe_log_missing_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGDIR", str(tmp_path))
    client = TestClient(app)
    res = client.get("/debug/mt5", headers={"X-Bridge-Secret": "bridge_secret_token"})
    assert res.status_code == 200
    assert res.json()["bootstrap"]["mt5_ipc_probe_log_exists"] is False


def test_wine_mt5_ipc_probe_script_matches_modes():
    from app.main import _wine_mt5_ipc_probe_script

    bare = _wine_mt5_ipc_probe_script(with_credentials=False, portable=False, timeout_ms=30000)
    assert "initialize(timeout=30000)" in bare
    assert "login=" not in bare

    cred = _wine_mt5_ipc_probe_script(with_credentials=True, portable=False, timeout_ms=60000)
    assert "login=123456" in cred
    assert "password='secret'" in cred
    assert "server='Broker-Demo'" in cred
    assert "portable=True" not in cred

    port = _wine_mt5_ipc_probe_script(with_credentials=True, portable=True, timeout_ms=60000)
    assert "portable=True" in port
