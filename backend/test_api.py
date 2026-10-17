import os
import tempfile

os.environ["DOCKET_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DOCKET_SEED"] = "0"

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_crud_roundtrip(client):
    r = client.post("/api/tasks", json={"title": "  Write tests  ", "priority": "high"})
    assert r.status_code == 201
    task = r.json()
    assert task["title"] == "Write tests" and task["status"] == "todo"

    assert client.get(f"/api/tasks/{task['id']}").json()["priority"] == "high"

    r = client.patch(f"/api/tasks/{task['id']}", json={"status": "done", "due_date": "2030-01-31"})
    assert r.status_code == 200
    assert r.json()["completed_at"] is not None and r.json()["due_date"] == "2030-01-31"

    r = client.patch(f"/api/tasks/{task['id']}", json={"status": "todo", "due_date": None})
    assert r.json()["completed_at"] is None and r.json()["due_date"] is None

    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404


def test_validation(client):
    assert client.post("/api/tasks", json={"title": ""}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "status": "nope"}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "evil": 1}).status_code == 422
    tid = client.post("/api/tasks", json={"title": "ok"}).json()["id"]
    assert client.patch(f"/api/tasks/{tid}", json={"title": None}).status_code == 422


def test_filter_search_and_stats(client):
    client.post("/api/tasks", json={"title": "Alpha 100%", "priority": "low"})
    client.post("/api/tasks", json={"title": "Beta", "status": "done", "due_date": "2000-01-01"})
    assert [t["title"] for t in client.get("/api/tasks", params={"q": "100%"}).json()] == ["Alpha 100%"]
    assert all(t["status"] == "done" for t in client.get("/api/tasks", params={"status": "done"}).json())
    s = client.get("/api/stats").json()
    assert s["total"] == s["todo"] + s["in_progress"] + s["done"]


def test_sql_injection_is_inert(client):
    r = client.get("/api/tasks", params={"q": "'; DROP TABLE tasks; --"})
    assert r.status_code == 200
    assert client.get("/api/tasks").status_code == 200
