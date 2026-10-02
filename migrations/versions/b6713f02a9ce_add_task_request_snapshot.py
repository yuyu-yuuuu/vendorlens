"""Persist the review input independently of the Redis job."""

import sqlalchemy as sa
from alembic import op

revision = "b6713f02a9ce"
down_revision = "9c4e1a7b2d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_tasks",
        sa.Column("request_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("research_tasks", "request_snapshot", server_default=None)


def downgrade() -> None:
    op.drop_column("research_tasks", "request_snapshot")
