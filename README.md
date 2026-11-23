# FlowBridge

A visual workflow automation platform with MCP integration, built for SPL-2 (IIT, University of Dhaka).

## Build plan

| Step | Deliverable | Status |
|---|---|---|
| 0 | Contracts: workflow JSON, data envelope, node interface, DB schema, API draft | **done (this delivery)** |
| 1 | Docker Compose skeleton: frontend, api, worker, redis, postgres, migrations | **done** |
| 2 | Workflow engine (pure Python) with the first nodes | **done** |
| 3 | API plus queue plus GitHub auth | **done** |
| 4 | React Flow editor and run viewer | **done** |
| 5 | Webhook delivery, idempotency, email | |
| 6 | MCP integration | |
| 7 | Hardening and docs | |

## What is in Step 0

```
docs/00-contracts.md        the contracts, read this first
docs/api-contract.md        REST API draft
contracts/                  generated JSON Schemas, node catalog, 3 example workflows
db/schema.sql               PostgreSQL reference schema
backend/app/contracts/      Pydantic models (source of truth), graph validator
backend/app/nodes/base.py   BaseNode, NodeContext, node registry
backend/tests/              pytest contract, API, and engine coverage
```

## Run the tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
```

The in-memory engine and its pytest-only tests are in `backend/app/engine/` and `backend/tests/test_engine.py`.
The engine validates workflow JSON, executes registered nodes in topological order, passes `Envelope` outputs
along active edges, and records in-memory node results and skipped states. The first implementation includes
manual/webhook triggers, data processing, conditions, and HTTP requests; database persistence remains for the API/worker steps.
Run just the engine tests with `python -m pytest tests/test_engine.py` from `backend/`.

After editing anything in `backend/app/contracts/`, regenerate the shared files:

```bash
python scripts/export_contracts.py
```

## Run the Step 1 stack

Install Docker Desktop (or Docker Engine with the Compose plugin), then run from the repository root:

```bash
docker compose up --build
```

The first API startup applies the Alembic migrations before serving requests. Once healthy:

* Frontend: <http://localhost:5173>
* API health: <http://localhost:8000/health>

The Compose stack includes the React/Vite frontend, FastAPI API, Celery worker, Redis broker, and PostgreSQL database.
PostgreSQL and Redis are available to the other services on the Compose network and are not published to the host.
The local database credentials in `compose.yaml` are development defaults; configure `POSTGRES_USER`, `POSTGRES_PASSWORD`,
and `POSTGRES_DB` for your environment. The named `postgres_data` volume preserves database data across restarts.
If a custom database password contains URL-reserved characters, also set `DATABASE_URL` with URL-encoded credentials.
Set `FLOWBRIDGE_FRONTEND_PORT` or `FLOWBRIDGE_API_PORT` if the default host ports are already in use.

For GitHub sign-in, register an OAuth App with callback URL
`http://localhost:8000/api/auth/github/callback`, then configure `GITHUB_CLIENT_ID`,
`GITHUB_CLIENT_SECRET`, and a strong `FLOWBRIDGE_SECRET_KEY` in your local `.env` file.
For production, use HTTPS, set `SESSION_COOKIE_SECURE=true`, and configure the public callback
and `FRONTEND_URL`. The Compose OAuth/session defaults are only for local development.

Stop the services with `docker compose down`. To also discard the local database, explicitly remove the volume with
`docker compose down --volumes`.

To run tests locally:

```bash
cd backend
pip install -r requirements.txt
python -m pytest
```

## Step 4 frontend

The editor is available at <http://localhost:5173> when the Compose stack is running. Sign in with the configured
GitHub OAuth app, create or open a workflow, add and connect nodes, edit node parameters in the inspector, and save
or run the workflow. Execution details are available at the run page; it refreshes every two seconds until the run
reaches a terminal status.

The frontend production bundle can be checked locally with `npm --prefix frontend ci` followed by
`npm --prefix frontend run build`.
