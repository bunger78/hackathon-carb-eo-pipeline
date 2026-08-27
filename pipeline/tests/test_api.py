from fastapi.testclient import TestClient
import main

def test_health():
    assert TestClient(main.app).get("/health").json() == {"ok": True}

def test_run_scheduled(monkeypatch):
    monkeypatch.setattr(main, "build_deps", lambda: object())
    monkeypatch.setattr(main, "run_workflow", lambda deps, trigger: {"status": "ok", "trigger": trigger})
    c = TestClient(main.app)
    resp = c.post("/run")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "trigger": "scheduled"}

def test_admin_requires_token(monkeypatch):
    monkeypatch.setattr(main, "build_deps", lambda: object())
    monkeypatch.setattr(main, "run_workflow", lambda deps, trigger: {"status": "ok"})
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    c = TestClient(main.app)
    assert c.post("/admin/run-now").status_code == 401
    ok = c.post("/admin/run-now", headers={"X-Admin-Token": "sekret"})
    assert ok.json()["status"] == "ok"

def test_admin_wrong_token_rejected(monkeypatch):
    monkeypatch.setattr(main, "build_deps", lambda: object())
    monkeypatch.setattr(main, "run_workflow", lambda deps, trigger: {"status": "ok"})
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    c = TestClient(main.app)
    resp = c.post("/admin/run-now", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401

def test_admin_no_token_configured_rejects_all(monkeypatch):
    monkeypatch.setattr(main, "build_deps", lambda: object())
    monkeypatch.setattr(main, "run_workflow", lambda deps, trigger: {"status": "ok"})
    monkeypatch.setattr(main, "ADMIN_TOKEN", "")
    c = TestClient(main.app)
    resp = c.post("/admin/run-now", headers={"X-Admin-Token": ""})
    assert resp.status_code == 401
