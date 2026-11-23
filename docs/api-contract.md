# FlowBridge: API contract (v1, draft for Step 3)

Base path `/api`. JSON in, JSON out. Auth is a session cookie set by the GitHub OAuth callback.
Every resource is scoped to the signed-in user; another user's id returns `404`, never `403`.

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
| POST | `/workflows/{id}/run` | Manual run | Creates `queued` execution, returns `202 { execution_id }`. |
| GET | `/workflows/{id}/executions` | Run list | |
| GET | `/executions/{id}` | Run detail | Execution plus its `node_runs`. Poll every 2 s. |
| POST | `/executions/{id}/cancel` | Cancel | Only while `queued` or `running`. |
| POST | `/hooks/{token}` (any method) | Webhook trigger | No auth, token is the secret. `202 { execution_id }`. Optional `Idempotency-Key`. |
| POST | `/workflows/{id}/webhook-token/rotate` | New token | Old URL stops working. |
| GET / POST / DELETE | `/credentials` | Manage credentials | Responses never include secret fields. |
| GET / POST / DELETE | `/mcp-servers` | Manage MCP servers | POST connects and caches the tool list. |
| POST | `/mcp-servers/{id}/refresh` | Re-fetch tools | |
| GET | `/mcp-servers/{id}/tools` | Cached tools | `[{name, description, input_schema}]` |
