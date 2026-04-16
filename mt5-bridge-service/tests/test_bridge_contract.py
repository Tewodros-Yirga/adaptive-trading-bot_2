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
    client = TestClient(app)
    denied = client.get("/account")
    assert denied.status_code == 403
    allowed = client.get("/account", headers={"X-Bridge-Secret": "bridge_secret_token"})
    assert allowed.status_code == 200
