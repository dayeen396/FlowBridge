"""Use an explicit creation timestamp for executions and ensure reliability indexes exist."""

from alembic import op

revision = "0002_execution_created_at"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("executions", "queued_at", new_column_name="created_at")
    op.execute(
        "ALTER INDEX IF EXISTS idx_executions_user_created "
        "RENAME TO idx_executions_user_created_at"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_user_created_at "
        "ON executions (user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_node_runs_execution "
        "ON node_runs (execution_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_executions_user_created_at")
    op.alter_column("executions", "created_at", new_column_name="queued_at")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_executions_user_created "
        "ON executions (user_id, queued_at DESC)"
    )
