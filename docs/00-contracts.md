# FlowBridge: Step 0 contracts (v1)

These contracts are the agreement between frontend, API, worker and database. Change one only by editing
`backend/app/contracts/` first, re-running `python backend/scripts/export_contracts.py`, and updating this file.
The test `test_generated_contract_files_are_up_to_date` fails if they drift apart.

## 1. Workflow definition (JSON)

Stored in `workflows.definition` and copied to `executions.definition_snapshot` when a run is queued.

```json
{
  "schema_version": 1,
  "name": "Big order alert",
  "description": "",
  "nodes": [
    {
      "id": "check",                       // ^[A-Za-z0-9_-]{1,64}$, unique in the workflow
      "type": "condition",                 // see section 5
      "name": "Amount > 1000",             // label shown on the canvas
      "params": { "left": "{{ item.amount }}", "operator": "gt", "right": 1000 },
      "position": { "x": 480, "y": 160 },  // canvas only, ignored by the engine
      "retry": { "max_attempts": 1, "backoff_seconds": 2, "backoff_multiplier": 2 },
      "timeout_seconds": 30,
      "on_error": "stop"                   // "stop" | "continue"
    }
  ],
  "edges": [
    { "id": "e3", "source": "check", "source_handle": "true", "target": "mail", "target_handle": "main" }
  ]
}
```

Graph rules (enforced by `validate_workflow`, on every save and again before every run):

| Code | Rule |
|---|---|
| `TRIGGER_COUNT` | Exactly one trigger node (`manual_trigger` or `webhook`). |
| `TRIGGER_HAS_INPUT` | A trigger has no incoming edges. |
| `ORPHAN_NODE` | Every non-trigger node has at least one incoming edge. |
| `CYCLE`, `SELF_LOOP` | The graph is a DAG. Loops are out of scope for v1. |
| `UNKNOWN_NODE` | Edge endpoints must exist. |
| `BAD_SOURCE_HANDLE` | `source_handle` must be an output of the source node type. |
| `DUPLICATE_*` | Node ids, edge ids and (source, handle, target) links are unique. |
| `UNREACHABLE` | Every node is reachable from the trigger. |
| `SCHEMA_ERROR` | Unknown fields, wrong types or `schema_version != 1`. |

Limits: 100 nodes, 300 edges, `timeout_seconds` 1 to 3600, `max_attempts` 1 to 10.

Save-time validation checks structure only. Node `params` are validated at run time, after expressions are
rendered, so half-finished forms can still be saved.

## 2. Data envelope

Nodes talk to each other only through `Envelope`:

```json
{ "items": [ { "data": { "email": "a@b.com", "amount": 1200 } } ], "meta": { "node_id": "shape", "duration_ms": 12 } }
```

* `items` is a list of records. Each `data` is a JSON object (never a bare string or array).
* `meta` is free-form engine information. Nodes never rely on it.
* A trigger node's input is one item holding the trigger payload (section 6).

## 3. Node result and branching

`execute()` returns a `NodeResult`:

```json
{ "status": "success", "outputs": { "true": { "items": [...] }, "false": { "items": [] } }, "error": null }
```

* `outputs` maps an **output handle** to an Envelope. Most nodes use `"main"`. `condition` uses `"true"` and `"false"`.
* An edge carries data when `outputs[edge.source_handle]` exists and has at least one item. Otherwise the edge is **inactive**.
* A node **runs** when at least one incoming edge is active. Its input is the concatenation of items from all active
  incoming edges, in the order the edges appear in the workflow.
* A node with no active incoming edge is **skipped** (`node_runs.status = skipped`). Skipping propagates downstream.
* `condition` evaluates every input item and routes it to `true` or `false`. A handle with no items is omitted.
* `status = failed` needs an `error`; `success` cannot have one.

## 4. Error format and retries

```json
{ "code": "HTTP_ERROR", "message": "502 from upstream", "retryable": true, "details": { "status": 502 } }
```

Codes: `VALIDATION_ERROR`, `EXPRESSION_ERROR`, `HTTP_ERROR`, `EMAIL_ERROR`, `MCP_ERROR`, `CREDENTIAL_ERROR`,
`TIMEOUT`, `NODE_ERROR`, `INTERNAL`.

* The engine retries a failed node only when `error.retryable` is true and attempts remain. `VALIDATION_ERROR`,
  `EXPRESSION_ERROR` and `CREDENTIAL_ERROR` are never retryable.
* Delay before attempt *n* (n >= 2) is `backoff_seconds * backoff_multiplier^(n-2)`.
* Each attempt writes its own `node_runs` row (`attempt` = 1, 2, 3...).
* After the last attempt fails: `on_error = "stop"` marks the execution `failed`; `"continue"` leaves the node
  `failed`, the execution carries on, and nodes reachable only through it are skipped.
* `timeout_seconds` applies per attempt and produces a retryable `TIMEOUT`.

## 5. Node types (v1)

| Type | Category | Outputs | Params (see `contracts/node-types.json` for full JSON Schema) | Output `data` |
|---|---|---|---|---|
| `manual_trigger` | trigger | main | `sample_data` | trigger payload |
| `webhook` | trigger | main | `http_method` | `{method, headers, query, body}` |
| `http_request` | action | main | `method, url, headers, query, body, fail_on_error_status` | `{status, headers, body}` |
| `data_processing` | logic | main | `operation (set/pick/rename/parse_json), fields, keys, source_key` | transformed item |
| `condition` | logic | true, false | `left, operator, right` | input item, unchanged |
| `send_email` | action | main | `credential_id, to, subject, body, from_address` | `{sent, to, subject, message_id}` |
| `mcp_tool` | integration | main | `server_id, tool_name, arguments` | `{content, structured, is_error}` |

`mcp_tool` is one node type for every tool. The frontend builds the arguments form from the tool's `input_schema`
stored in `mcp_servers.tools_cache`.

Condition operators: `equals, not_equals, contains, gt, gte, lt, lte, is_empty, is_not_empty`.

## 6. Trigger payload

```json
{ "type": "webhook", "data": { "method": "POST", "headers": {}, "query": {}, "body": { "amount": 1200 } } }
```

Manual runs use `{ "type": "manual", "data": <sample_data> }`. Stored in `executions.trigger_payload`.

## 7. Expressions

Any string inside `params` may contain `{{ path }}`. No code is ever evaluated, so there is no `eval` risk.

| Root | Meaning |
|---|---|
| `item` | `data` of the current input item. Nodes that work per item (`condition`, `data_processing`) render once per item; all other nodes use the first item. |
| `items` | list of all input items' `data` |
| `trigger` | the trigger payload's `data` |
| `nodes.<id>.items` | items of that finished node's `main` output. Use `nodes.<id>.<handle>.items` for other handles. |
| `execution.id` | the execution id |

Path grammar: names separated by `.`, list access with `[n]`, for example `item.body.users[0].email`.

* A string that is **only** one expression keeps its type: `"{{ item.amount }}"` becomes the number `1200`.
* An expression inside longer text becomes a string: `"Order from {{ item.customer }}"`.
* A missing key resolves to `null`. Bad syntax raises `EXPRESSION_ERROR` (not retryable).
* Secrets are never available to expressions. Nodes read credentials through `ctx.credentials`.

## 8. Node interface (Python)

```python
class BaseNode(ABC):
    node_type: ClassVar[NodeType]
    async def execute(self, input: Envelope, params: BaseModel, ctx: NodeContext) -> NodeResult: ...
```

The engine handles rendering, params validation, retries, timeouts, persistence and skipping. A node only does its
job and either returns `NodeResult.ok(...)` or raises `NodeFailure(code, message, retryable)`.
Registration: `@register_node` on the class, whose `node_type` must exist in `NODE_TYPE_SPECS`.
`NodeContext` carries `execution_id, workflow_id, user_id, node_id, attempt, trigger, node_outputs, credentials, logger`.

Run pipeline per node: pick input, render expressions, validate with `params_model`, `execute()`, write `node_runs`.

## 9. Execution lifecycle

```
execution:  queued -> running -> success | failed | cancelled
node_run:   pending -> running -> success | failed | skipped
```

* The API creates the execution as `queued` (with `definition_snapshot` and `trigger_payload`) and enqueues its id.
* A worker moves it to `running`, executes nodes in `topological_order`, then sets the final status.
* The worker only touches an execution whose status is `queued`, so a duplicated job never runs twice.
* `idempotency_key` (optional, from the `Idempotency-Key` header on webhooks) is unique per workflow.

## 10. Database

See `db/schema.sql`. Tables: `users`, `credentials`, `mcp_servers`, `workflows`,
`executions`, `node_runs`. Key decisions:

* UUID keys and `timestamptz` everywhere. Every user-owned row has `user_id`; every query filters by it.
* `definition` and run payloads are JSONB, so the graph format can grow without migrations.
* `executions.definition_snapshot` freezes the graph at queue time.
* `workflows.version` is bumped on each save; the API rejects a save whose `version` is stale (409).
* Credentials are encrypted in the application (Fernet) and never returned by the API.
* Retention: v1 keeps everything. A cleanup job for old `node_runs` is future work.

## 11. Decisions log

| # | Decision | Why |
|---|---|---|
| D1 | Items use `{ "data": ... }` | Avoids clashing with the `json` method on Pydantic models. |
| D2 | DAG only, no loops | Keeps the engine small; loops are future work. |
| D3 | Exactly one trigger per workflow | Simple model, matches webhook plus manual use. |
| D4 | Params validated at run time | Lets users save unfinished nodes. |
| D5 | Async `execute()` | HTTP, SMTP and MCP calls are all I/O bound. |
| D6 | Absent handle = inactive branch | One rule covers `condition` routing and skipping. |
| D7 | Snapshot the definition per run | Editing a workflow never changes a run in flight. |
| D8 | MCP transports: streamable HTTP and SSE only | stdio would let users run arbitrary commands on the host. |
| D9 | No code evaluation in expressions | Security and simplicity. |
