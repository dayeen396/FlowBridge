# FlowBridge: API contract (v1)

Base path `/api`. JSON in, JSON out. Auth is a signed session cookie set by the GitHub OAuth callback.
Every resource is scoped to the signed-in user; another user's id returns `404`, never `403`.
The liveness endpoint is available at both `/health` (container health check) and `/api/health`.
The callback URL and OAuth credentials are configured with `GITHUB_REDIRECT_URI`,
`GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET`. Set `FLOWBRIDGE_SECRET_KEY` to a strong random
value outside local development; set `SESSION_COOKIE_SECURE=true` when serving over HTTPS.

Errors always look like `{ "error": { "code": "...", "message": "...", "issues": [...] } }`.
`issues` appears on workflow validation failures and holds `ValidationIssue` objects.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| GET | `/health` | Liveness | No auth. |
| GET | `/auth/github/login` | Start OAuth | Redirects to GitHub. |
| GET | `/auth/github/callback` | Finish OAuth | Sets cookie, redirects to the app. |
| POST | `/auth/logout` | End session | |
| GET | `/me` | Current user | |
| GET | `/node-types` | Node catalog | Same shape as `contracts/node-types.json`. |
| GET | `/workflows` | List own workflows | Pagination: `limit`, `cursor`. |
| POST | `/workflows` | Create | Body: workflow definition. `422` with `issues` if invalid. |
| GET | `/workflows/{id}` | Read | Returns definition, `version`, `webhook_url`. |
| PUT | `/workflows/{id}` | Save | Body needs `version`; stale version gives `409`. |
| DELETE | `/workflows/{id}` | Delete | |
| POST | `/workflows/{id}/run` | Manual run | Creates `queued` execution, enqueues the Celery job, returns `202 { execution_id }`. Optional body: `{ "data": {...} }`; defaults to trigger `sample_data`. |
| GET | `/workflows/{id}/executions` | Run list | |
| GET | `/executions/{id}` | Run detail | Execution plus its `node_runs`. Poll every 2 s. |
| POST | `/executions/{id}/cancel` | Cancel | Only while `queued` or `running`. |
| POST | `/hooks/{token}` (any method) | Webhook trigger | No auth, token is the secret. `202 { execution_id }`. Optional `Idempotency-Key`. |
| POST | `/workflows/{id}/webhook-token/rotate` | New token | Old URL stops working. |
| GET / POST / DELETE | `/credentials` | Manage credentials | Responses never include secret fields. |
| GET / POST / DELETE | `/mcp-servers` | Manage MCP servers | POST connects and caches the tool list. Supports `streamable_http` and `sse`; optional `credential_id` references an `mcp_auth` credential. |
| POST | `/mcp-servers/{id}/refresh` | Re-fetch tools | |
| GET | `/mcp-servers/{id}/tools` | Cached tools | `[{name, description, input_schema}]` |

The API supports GitHub OAuth/session authentication, node catalog, workflow CRUD, manual execution,
webhook delivery, execution history, and SMTP credential management. Webhook tokens are generated per
workflow, can be rotated, and are returned only in the authenticated workflow detail. Webhooks accept
the configured method, return `202`, and optionally deduplicate requests with `Idempotency-Key`.
SMTP credentials are created with `POST /credentials` using `{ "name": "Mail", "type": "smtp",
"data": { "host": "smtp.example.com", "port": 587, "username": "...", "password": "...",
"starttls": true, "from_address": "..." } }`; list responses never include credential data.
The worker claims only queued executions, runs the frozen definition snapshot, stores each node
attempt, and applies per-node plus `FLOWBRIDGE_RUN_TIMEOUT_SECONDS` total timeouts.
MCP server records are scoped to the signed-in user. `POST /mcp-servers` connects before saving; refresh replaces
the cached tool list and schemas after a successful `tools/list`. MCP auth credentials can contain either a
`bearer_token` or a `headers` object and are encrypted like SMTP credentials. The node editor reads the cached
schemas to render each selected tool's argument fields. MCP connections use the official Python SDK over
Streamable HTTP or SSE; stdio is intentionally unsupported.
