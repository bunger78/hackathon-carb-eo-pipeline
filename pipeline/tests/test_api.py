from fastapi.testclient import TestClient
import main
from runner import Deps
from core.costs import BudgetGuard
from matching.engine import VehicleIndex
from tests.fakes import FakeLLM, FakeRepo

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

def test_resolve_review_bad_action_rejected(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    c = TestClient(main.app)
    resp = c.post("/admin/resolve-review", json={"review_id": "r1", "action": "bogus"},
                  headers={"X-Admin-Token": "sekret"})
    assert resp.status_code == 422

def test_resolve_review_unknown_id_404(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    deps = Deps(repo=FakeRepo(), llm=FakeLLM([]), gcs=None, carb=None,
                index=VehicleIndex([]), budget=BudgetGuard(5))
    monkeypatch.setattr(main, "build_deps", lambda: deps)
    c = TestClient(main.app)
    resp = c.post("/admin/resolve-review", json={"review_id": "nope", "action": "approve"},
                  headers={"X-Admin-Token": "sekret"})
    assert resp.status_code == 404

def test_retry_eo_requires_token(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    c = TestClient(main.app)
    resp = c.post("/admin/retry-eo", json={"eo_number": "D-1-1"})
    assert resp.status_code == 401

def test_retry_eo_unknown_404(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    deps = Deps(repo=FakeRepo(), llm=FakeLLM([]), gcs=None, carb=None,
                index=VehicleIndex([]), budget=BudgetGuard(5))
    monkeypatch.setattr(main, "build_deps", lambda: deps)
    c = TestClient(main.app)
    resp = c.post("/admin/retry-eo", json={"eo_number": "D-999-1"},
                  headers={"X-Admin-Token": "sekret"})
    assert resp.status_code == 404

def test_retry_eo_non_failed_409(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    repo = FakeRepo()
    repo.create_work_item("D-1-1", "run0")  # freshly created -> status "pending", not "failed"
    deps = Deps(repo=repo, llm=FakeLLM([]), gcs=None, carb=None,
                index=VehicleIndex([]), budget=BudgetGuard(5))
    monkeypatch.setattr(main, "build_deps", lambda: deps)
    c = TestClient(main.app)
    resp = c.post("/admin/retry-eo", json={"eo_number": "D-1-1"},
                  headers={"X-Admin-Token": "sekret"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"eo_number": "D-1-1", "status": "pending"}

def test_retry_eo_happy_path(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_TOKEN", "sekret")
    repo = FakeRepo()
    item_id = repo.create_work_item("D-1-1", "run0")
    repo.update_work_item(item_id, {"status": "failed", "attempts": 3, "last_error": "boom"})
    deps = Deps(repo=repo, llm=FakeLLM([]), gcs=None, carb=None,
                index=VehicleIndex([]), budget=BudgetGuard(5))
    monkeypatch.setattr(main, "build_deps", lambda: deps)
    c = TestClient(main.app)
    resp = c.post("/admin/retry-eo", json={"eo_number": "D-1-1"},
                  headers={"X-Admin-Token": "sekret"})
    assert resp.status_code == 200
    assert resp.json() == {"eo_number": "D-1-1", "requeued": True}
    assert repo.get_eo("D-1-1")["state"] == "discovered"
