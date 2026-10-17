"""Docket - task dashboard API.

FastAPI + SQLite. Serves the REST API under /api and the static frontend at /.

Environment variables (all optional):
    DOCKET_DB        path to the SQLite file          (default: ./docket.db)
    DOCKET_API_KEY   if set, every /api call must send header X-API-Key
    DOCKET_CORS      comma-separated origins allowed to call the API cross-origin
    DOCKET_SEED      set to "0" to skip the demo tasks on a fresh database
"""
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
DB_PATH = os.environ.get("DOCKET_DB", str(BASE_DIR / "docket.db"))
API_KEY = os.environ.get("DOCKET_API_KEY", "")
CORS_ORIGINS = [o.strip() for o in os.environ.get("DOCKET_CORS", "").split(",") if o.strip()]

Status = Literal["todo", "in_progress", "done"]
Priority = Literal["low", "medium", "high"]

SORTS = {  # whitelist: user input only ever selects a key, never reaches SQL
    "created": "created_at DESC, id DESC",
    "due": "due_date IS NULL, due_date ASC, id DESC",
    "priority": "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id DESC",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    description  TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    status       TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','in_progress','done')),
    priority     TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low','medium','high')),
    due_date     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        empty = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        if empty and os.environ.get("DOCKET_SEED", "1") != "0":
            ts = now_iso()
            demo = [
                ("Draft the project proposal", "Outline scope, timeline and budget for review.", "in_progress", "high", None),
                ("Review pull requests", "Two open PRs on the billing service.", "todo", "medium", None),
                ("Book dentist appointment", "", "todo", "low", None),
                ("Set up CI pipeline", "Run tests and lint on every push.", "done", "medium", None),
            ]
            for title, desc, status, prio, due in demo:
                conn.execute(
                    "INSERT INTO tasks (title, description, status, priority, due_date, created_at, updated_at, completed_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (title, desc, status, prio, due, ts, ts, ts if status == "done" else None),
                )
        conn.commit()
    finally:
        conn.close()


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    status: Status = "todo"
    priority: Priority = "medium"
    due_date: Optional[date] = None


class TaskUpdate(BaseModel):
    """Partial update: only fields present in the request body are changed."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    due_date: Optional[date] = None  # explicit null clears the date


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key.")


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Docket API", version="1.0.0", lifespan=lifespan)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'",
        )
    return response


api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])


def fetch_task(db: sqlite3.Connection, task_id: int) -> dict:
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found.")
    return dict(row)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


# READ (list) ---------------------------------------------------------------
@api.get("/tasks", tags=["tasks"])
def list_tasks(
    status: Optional[Status] = None,
    priority: Optional[Priority] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    sort: Literal["created", "due", "priority"] = "created",
    db: sqlite3.Connection = Depends(get_db),
):
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
        params += [f"%{escaped}%"] * 2
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + SORTS[sort]
    return [dict(r) for r in db.execute(sql, params).fetchall()]


# READ (stats) --------------------------------------------------------------
@api.get("/stats", tags=["tasks"])
def stats(db: sqlite3.Connection = Depends(get_db)):
    counts = {"todo": 0, "in_progress": 0, "done": 0}
    for row in db.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
        counts[row["status"]] = row["n"]
    total = sum(counts.values())
    overdue = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE status != 'done' AND due_date IS NOT NULL AND due_date < ?",
        (date.today().isoformat(),),
    ).fetchone()[0]
    return {
        **counts,
        "total": total,
        "overdue": overdue,
        "completion_rate": round(counts["done"] / total * 100) if total else 0,
    }


# READ (one) ----------------------------------------------------------------
@api.get("/tasks/{task_id}", tags=["tasks"])
def get_task(task_id: int, db: sqlite3.Connection = Depends(get_db)):
    return fetch_task(db, task_id)


# CREATE --------------------------------------------------------------------
@api.post("/tasks", status_code=201, tags=["tasks"])
def create_task(payload: TaskCreate, db: sqlite3.Connection = Depends(get_db)):
    ts = now_iso()
    cur = db.execute(
        "INSERT INTO tasks (title, description, status, priority, due_date, created_at, updated_at, completed_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            payload.title,
            payload.description,
            payload.status,
            payload.priority,
            payload.due_date.isoformat() if payload.due_date else None,
            ts,
            ts,
            ts if payload.status == "done" else None,
        ),
    )
    db.commit()
    return fetch_task(db, cur.lastrowid)


# UPDATE --------------------------------------------------------------------
@api.patch("/tasks/{task_id}", tags=["tasks"])
@api.put("/tasks/{task_id}", tags=["tasks"])
def update_task(task_id: int, payload: TaskUpdate, db: sqlite3.Connection = Depends(get_db)):
    current = fetch_task(db, task_id)
    changes = payload.model_dump(exclude_unset=True)

    for field in ("title", "description", "status", "priority"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"'{field}' cannot be null.")
    if "due_date" in changes and changes["due_date"] is not None:
        changes["due_date"] = changes["due_date"].isoformat()

    if "status" in changes and changes["status"] != current["status"]:
        changes["completed_at"] = now_iso() if changes["status"] == "done" else None

    if changes:
        changes["updated_at"] = now_iso()
        # Column names come from the validated model above, never from raw input.
        assignments = ", ".join(f"{col} = ?" for col in changes)
        db.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*changes.values(), task_id))
        db.commit()
    return fetch_task(db, task_id)


# DELETE --------------------------------------------------------------------
@api.delete("/tasks/{task_id}", status_code=204, tags=["tasks"])
def delete_task(task_id: int, db: sqlite3.Connection = Depends(get_db)):
    fetch_task(db, task_id)
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()


app.include_router(api)

# Frontend last, so /api/* routes win.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
