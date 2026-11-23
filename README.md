# FlowBridge

A visual workflow automation platform with MCP integration, built for SPL-2 (IIT, University of Dhaka).

## Build plan

| Step | Deliverable | Status |
|---|---|---|
| 0 | Contracts: workflow JSON, data envelope, node interface, DB schema, API draft | **done (this delivery)** |
| 1 | Docker Compose skeleton: frontend, api, worker, redis, postgres, migrations | next |
| 2 | Workflow engine (pure Python) with the first nodes | |
| 3 | API plus queue plus GitHub auth | |
| 4 | React Flow editor and run viewer | |
| 5 | Webhooks, retries, idempotency, email | |
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
backend/tests/              30 tests
```

## Run the tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
```

After editing anything in `backend/app/contracts/`, regenerate the shared files:

```bash
python scripts/export_contracts.py
```
