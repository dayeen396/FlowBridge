"""Create the Step 0 PostgreSQL schema.

Revision ID: 0001_initial
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE TABLE users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            github_id bigint NOT NULL UNIQUE,
            login text NOT NULL,
            name text,
            email text,
            avatar_url text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE credentials (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name text NOT NULL,
            type text NOT NULL CHECK (type IN ('smtp', 'http_header', 'mcp_auth', 'generic')),
            data_encrypted bytea NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (user_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE mcp_servers (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name text NOT NULL,
            transport text NOT NULL CHECK (transport IN ('streamable_http', 'sse')),
            url text NOT NULL,
            credential_id uuid REFERENCES credentials(id) ON DELETE SET NULL,
            tools_cache jsonb NOT NULL DEFAULT '[]'::jsonb,
            tools_refreshed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (user_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE workflows (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name text NOT NULL,
            description text NOT NULL DEFAULT '',
            definition jsonb NOT NULL,
            schema_version integer NOT NULL DEFAULT 1,
            version integer NOT NULL DEFAULT 1,
            is_active boolean NOT NULL DEFAULT true,
            webhook_token text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_workflows_user ON workflows (user_id, updated_at DESC)")
    op.execute(
        """
        CREATE TABLE executions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workflow_id uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
            trigger_type text NOT NULL CHECK (trigger_type IN ('manual', 'webhook')),
            trigger_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            definition_snapshot jsonb NOT NULL,
            idempotency_key text,
            error jsonb,
            queued_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz
        )
        """
    )
    op.execute("CREATE INDEX idx_executions_user_created ON executions (user_id, queued_at DESC)")
    op.execute("CREATE INDEX idx_executions_workflow ON executions (workflow_id, queued_at DESC)")
    op.execute("CREATE INDEX idx_executions_status ON executions (status) WHERE status IN ('queued', 'running')")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_executions_idempotency ON executions (workflow_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE node_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            execution_id uuid NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
            node_id text NOT NULL,
            node_type text NOT NULL,
            node_name text NOT NULL,
            attempt integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'success', 'failed', 'skipped')),
            input jsonb,
            output jsonb,
            error jsonb,
            started_at timestamptz,
            finished_at timestamptz,
            duration_ms integer,
            UNIQUE (execution_id, node_id, attempt)
        )
        """
    )
    op.execute("CREATE INDEX idx_node_runs_execution ON node_runs (execution_id)")


def downgrade() -> None:
    op.execute("DROP TABLE node_runs")
    op.execute("DROP TABLE executions")
    op.execute("DROP TABLE workflows")
    op.execute("DROP TABLE mcp_servers")
    op.execute("DROP TABLE credentials")
    op.execute("DROP TABLE users")
