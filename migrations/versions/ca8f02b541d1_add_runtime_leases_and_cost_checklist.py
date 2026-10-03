"""Execution fencing leases and configurable required costs."""
import sqlalchemy as sa
from alembic import op

revision = "ca8f02b541d1"
down_revision = "b6713f02a9ce"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("research_tasks") as batch:
        batch.add_column(sa.Column("lease_owner", sa.String(36), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("resume_requested", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    with op.batch_alter_table("review_configs") as batch:
        batch.add_column(sa.Column("required_cost_categories", sa.JSON(), nullable=False,
                                   server_default='["software", "implementation", "maintenance"]'))


def downgrade():
    with op.batch_alter_table("review_configs") as batch:
        batch.drop_column("required_cost_categories")
    with op.batch_alter_table("research_tasks") as batch:
        for name in ("resume_requested", "attempt", "lease_expires_at", "lease_owner"):
            batch.drop_column(name)
