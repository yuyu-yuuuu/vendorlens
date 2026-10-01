"""add agent runs and resumable events

Revision ID: 24a805a329aa
Revises: 1530507c7f1f
Create Date: 2026-10-01 11:05:44
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "24a805a329aa"
down_revision: str | Sequence[str] | None = "1530507c7f1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"], unique=False)
    op.create_table(
        "run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "sequence"),
    )
    op.create_index("ix_run_events_task_id", "run_events", ["task_id"], unique=False)
    op.create_index(
        "ix_run_events_task_sequence", "run_events", ["task_id", "sequence"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_run_events_task_sequence", table_name="run_events")
    op.drop_index("ix_run_events_task_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_index("ix_agent_runs_task_id", table_name="agent_runs")
    op.drop_table("agent_runs")
