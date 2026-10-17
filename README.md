# Docket: Interactive Task Dashboard

Full-stack CRUD app. Async vanilla-JS frontend (`fetch` + `async/await`) talking to a
FastAPI microservice backed by a local SQLite database.

## Run it

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000 (dashboard) or http://127.0.0.1:8000/docs (interactive API docs).
The database file `backend/docket.db` is created on first start with four demo tasks.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/tasks?status=&priority=&q=&sort=created\|due\|priority` | List / filter / search |
| GET | `/api/tasks/{id}` | Read one |
| POST | `/api/tasks` | Create |
| PATCH / PUT | `/api/tasks/{id}` | Update (partial; `due_date: null` clears the date) |
| DELETE | `/api/tasks/{id}` | Delete (204) |
| GET | `/api/stats` | Counts, overdue, completion rate |
| GET | `/api/health` | Liveness check |

Task fields: `title` (1-120), `description` (<=1000), `status` (`todo`/`in_progress`/`done`),
`priority` (`low`/`medium`/`high`), `due_date` (`YYYY-MM-DD` or null).

## Security measures

- Parameterised SQL everywhere; sort order chosen from a whitelist; `LIKE` wildcards escaped.
- Pydantic validation on every body (types, lengths, enums, unknown fields rejected).
- DB-level `CHECK` constraints as a second line of defence.
- Optional API key: `export DOCKET_API_KEY=secret` makes every `/api` call require
  an `X-API-Key` header (constant-time compare). The UI prompts for it on a 401.
- CORS closed by default; opt in with `DOCKET_CORS=https://yourapp.example`.
- Security headers incl. a strict Content-Security-Policy; the frontend renders all user
  text with `textContent`, so stored text can't inject HTML.

## Features

- Create, edit, delete (two-click confirm), and move tasks between To do / In progress / Done
  by button or drag-and-drop
- Live search, priority filter, sorting
- Progress bar and counters driven by `/api/stats`
- Overdue highlighting, toast feedback, server validation errors shown in the form
- Keyboard-accessible, responsive, respects reduced-motion

## Structure

```
docket/
├── backend/   main.py  test_api.py  requirements*.txt
├── frontend/  index.html  style.css  app.js
└── README.md
```

## Environment variables

`DOCKET_DB` (db path), `DOCKET_API_KEY`, `DOCKET_CORS`, `DOCKET_SEED=0` (no demo data).
