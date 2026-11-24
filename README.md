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
| 5 | Webhook delivery, idempotency, email | **done** |
| 6 | MCP integration | **done** |
| 7 | Hardening and docs | **done** |

## What is in Step 0

```
docs/00-contracts.md        the contracts, read this first
docs/api-contract.md        REST API draft
contracts/                  generated JSON Schemas, node catalog, 4 example workflows
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

## Step 5 triggers and reliability

Saved workflows with a Webhook trigger show a copyable URL in the editor. Rotate its token there to invalidate
the previous URL. Webhook requests must use the trigger's configured method; send `Idempotency-Key` to reuse
the original execution for a retried delivery. Duplicate Celery deliveries cannot claim an execution already
marked `running`.

Node retry and timeout settings are available in the node inspector. Total workflow time is capped by
`FLOWBRIDGE_RUN_TIMEOUT_SECONDS` (default 3600). Send Email nodes use an SMTP credential created from the
node inspector; credential secrets are encrypted with a key derived from `FLOWBRIDGE_SECRET_KEY` and are
never returned by the API. Configure the same secret key for the API and worker.

## Step 6 MCP integration

Open **MCP servers** in the workflow editor, add a server URL using Streamable HTTP or SSE, and optionally
provide a bearer token. FlowBridge connects and caches the server's tool names, descriptions, and input schemas.
Refresh the cache after the remote server changes its tools. Add an MCP Tool node, select the server and tool,
and configure the generated argument fields; the worker reconnects to the server to call the selected tool.

## Self-hosting

1. Install Docker Engine with the Compose plugin or Docker Desktop, then clone this repository.
2. Create a local environment file and replace the database password and secret:

   ```bash
   cp .env.example .env
   python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

   Put the generated value in `FLOWBRIDGE_SECRET_KEY`. Keep this value stable and set the same value for the API
   and worker. It protects sessions and encrypts credentials.
3. Create a GitHub OAuth App with callback URL `http://localhost:8000/api/auth/github/callback`. Add its client ID
   and secret to `.env`. For remote hosting, set `GITHUB_REDIRECT_URI`, `FRONTEND_URL`, and
   `SESSION_COOKIE_SECURE=true` to the HTTPS URLs you serve.
4. Start the stack from the project root:

   ```bash
   docker compose up --build -d
   docker compose ps
   ```

   The API applies database migrations during startup. Open <http://localhost:5173>; health is at
   <http://localhost:8000/health>. Follow startup logs with `docker compose logs -f api worker`.
5. Sign in through GitHub, create a workflow, and run the demo described below. PostgreSQL and Redis stay on the
   internal Compose network. Change the host ports with `FLOWBRIDGE_FRONTEND_PORT` and `FLOWBRIDGE_API_PORT`.
6. Stop services with `docker compose down`. This preserves database data. To delete all stored data as well,
   run `docker compose down --volumes`.

Before exposing a host publicly, use strong unique database credentials and `FLOWBRIDGE_SECRET_KEY`, configure
HTTPS and secure cookies, and restrict access to ports 5173 and 8000 according to your deployment. Back up the
PostgreSQL volume regularly; workflow definitions, execution history, and encrypted credentials are stored there.

## Demo workflow

The complete graph is in [04-order-triage-demo.json](contracts/examples/04-order-triage-demo.json). It is
self-contained and needs no external services. In the editor, create these connected steps:

1. **Manual Trigger** with sample data `{"order":{"id":"ORD-1042","customer":"Ava","total":1250}}`.
2. **Data Processing** (`set`) with fields `order_id: {{ item.order.id }}`, `customer: {{ item.order.customer }}`,
   and `total: {{ item.order.total }}`.
3. **Condition** with left `{{ item.total }}`, operator `gt`, and right `1000`.
4. Connect the condition’s **true** output to a Data Processing node that sets `priority: review`; connect
   **false** to one that sets `priority: standard`.

Run it and inspect the execution. The sample order appears in the `review` branch, and the `standard` node is
skipped. See the [architecture overview](docs/architecture.md) for service boundaries and data flow.
