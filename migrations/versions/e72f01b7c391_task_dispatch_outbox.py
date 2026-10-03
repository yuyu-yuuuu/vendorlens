"""transactional task dispatch

Revision ID: e72f01b7c391
Revises: d61a93f0e482
"""

import sqlalchemy as sa
from alembic import op

revision = "e72f01b7c391"
down_revision = "d61a93f0e482"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_dispatches",
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("research_tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("job_id", sa.String(120), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(100)),
    )
    # Pending tasks created by older versions also acquire a durable dispatch record.
    op.execute(
        sa.text(
            "INSERT INTO task_dispatches (task_id, job_id, next_attempt_at, attempts) "
            "SELECT id, run_id, CURRENT_TIMESTAMP, 0 FROM research_tasks "
            "WHERE status IN ('queued', 'enqueue_failed')"
        )
    )


def downgrade():
    op.drop_table("task_dispatches")
