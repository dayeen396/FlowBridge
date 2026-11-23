-- FlowBridge reference schema (PostgreSQL 15+). Step 1 turns this into Alembic migration 0001.
-- Rules: UUID primary keys, timestamptz everywhere, every user-owned row carries user_id,
-- all workflow/node payloads are JSONB.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ---------------------------------------------------------------- users
CREATE TABLE users (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id   bigint      NOT NULL UNIQUE,
    login       text        NOT NULL,
    name        text,
    email       text,
    avatar_url  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------ credentials
-- Encrypted at the application layer (Fernet, key from FLOWBRIDGE_SECRET_KEY). Never returned by the API.
CREATE TABLE credentials (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    type            text        NOT NULL CHECK (type IN ('smtp', 'http_header', 'mcp_auth', 'generic')),
    data_encrypted  bytea       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

-- ------------------------------------------------------------ mcp_servers
CREATE TABLE mcp_servers (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              text        NOT NULL,
    transport         text        NOT NULL CHECK (transport IN ('streamable_http', 'sse')),
    url               text        NOT NULL,
    credential_id     uuid        REFERENCES credentials(id) ON DELETE SET NULL,
    tools_cache       jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{name, description, input_schema}]
    tools_refreshed_at timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, name)
);

-- -------------------------------------------------------------- workflows
CREATE TABLE workflows (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            text        NOT NULL,
    description     text        NOT NULL DEFAULT '',
    definition      jsonb       NOT NULL,                  -- the Workflow contract (nodes + edges)
    schema_version  integer     NOT NULL DEFAULT 1,
    version         integer     NOT NULL DEFAULT 1,        -- bumped on every save (optimistic locking)
    is_active       boolean     NOT NULL DEFAULT true,     -- inactive workflows ignore webhooks
    webhook_token   text        NOT NULL UNIQUE,           -- random 32+ bytes, used in /hooks/{token}
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_workflows_user ON workflows (user_id, updated_at DESC);

-- ------------------------------------------------------------- executions
CREATE TABLE executions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id         uuid        NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    user_id             uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status              text        NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
    trigger_type        text        NOT NULL CHECK (trigger_type IN ('manual', 'webhook')),
    trigger_payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    definition_snapshot jsonb       NOT NULL,              -- edits after queueing never change a run
    idempotency_key     text,
    error               jsonb,                             -- NodeError-shaped, set when status = failed
    queued_at           timestamptz NOT NULL DEFAULT now(),
    started_at          timestamptz,
    finished_at         timestamptz
);
CREATE INDEX idx_executions_user_created ON executions (user_id, queued_at DESC);
CREATE INDEX idx_executions_workflow     ON executions (workflow_id, queued_at DESC);
CREATE INDEX idx_executions_status       ON executions (status) WHERE status IN ('queued', 'running');
CREATE UNIQUE INDEX uq_executions_idempotency ON executions (workflow_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- --------------------------------------------------------------- node_runs
-- One row per node per ATTEMPT. A retried node has several rows with increasing attempt.
CREATE TABLE node_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id  uuid        NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    node_id       text        NOT NULL,                    -- id inside definition_snapshot
    node_type     text        NOT NULL,
    node_name     text        NOT NULL,
    attempt       integer     NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    status        text        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'running', 'success', 'failed', 'skipped')),
    input         jsonb,                                   -- Envelope
    output        jsonb,                                   -- {handle: Envelope}
    error         jsonb,                                   -- NodeError
    started_at    timestamptz,
    finished_at   timestamptz,
    duration_ms   integer,
    UNIQUE (execution_id, node_id, attempt)
);
CREATE INDEX idx_node_runs_execution ON node_runs (execution_id);
