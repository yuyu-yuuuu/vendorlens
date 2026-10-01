"""add vendor review domain

Revision ID: 9c4e1a7b2d30
Revises: f2b6e739f68b
Create Date: 2026-10-01 17:50:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c4e1a7b2d30"
down_revision: str | Sequence[str] | None = "f2b6e739f68b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index("ix_vendors_project_id", "vendors", ["project_id"], unique=False)

    op.add_column("documents", sa.Column("vendor_id", sa.String(length=36), nullable=True))
    op.create_index("ix_documents_vendor_id", "documents", ["vendor_id"], unique=False)
    op.create_foreign_key(
        "fk_documents_vendor_id",
        "documents",
        "vendors",
        ["vendor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("documents_project_id_logical_name_key", "documents", type_="unique")
    op.create_index(
        "uq_documents_buyer_logical_name",
        "documents",
        ["project_id", "logical_name"],
        unique=True,
        postgresql_where=sa.text("vendor_id IS NULL"),
    )
    op.create_index(
        "uq_documents_vendor_logical_name",
        "documents",
        ["vendor_id", "logical_name"],
        unique=True,
        postgresql_where=sa.text("vendor_id IS NOT NULL"),
    )

    op.create_table(
        "requirements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_chunk_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chunk_id"], ["document_chunks.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "code"),
    )
    op.create_index("ix_requirements_project_id", "requirements", ["project_id"], unique=False)
    op.create_index(
        "ix_requirements_source_chunk_id", "requirements", ["source_chunk_id"], unique=False
    )

    op.create_table(
        "review_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("requirement_weight", sa.Float(), nullable=False),
        sa.Column("cost_weight", sa.Float(), nullable=False),
        sa.Column("commitment_weight", sa.Float(), nullable=False),
        sa.Column("comparison_months", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("commitment_checklist", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )

    op.create_table(
        "domain_assessments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("vendor_id", sa.String(length=36), nullable=False),
        sa.Column("domain", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("missing_information", sa.JSON(), nullable=False),
        sa.Column("conflicts", sa.JSON(), nullable=False),
        sa.Column("revision_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["research_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "vendor_id", "domain"),
    )
    op.create_index(
        "ix_domain_assessments_task_id", "domain_assessments", ["task_id"], unique=False
    )
    op.create_index(
        "ix_domain_assessments_vendor_id",
        "domain_assessments",
        ["vendor_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_domain_assessments_vendor_id", table_name="domain_assessments")
    op.drop_index("ix_domain_assessments_task_id", table_name="domain_assessments")
    op.drop_table("domain_assessments")
    op.drop_table("review_configs")
    op.drop_index("ix_requirements_source_chunk_id", table_name="requirements")
    op.drop_index("ix_requirements_project_id", table_name="requirements")
    op.drop_table("requirements")

    op.drop_index("uq_documents_vendor_logical_name", table_name="documents")
    op.drop_index("uq_documents_buyer_logical_name", table_name="documents")
    op.create_unique_constraint(
        "documents_project_id_logical_name_key",
        "documents",
        ["project_id", "logical_name"],
    )
    op.drop_constraint("fk_documents_vendor_id", "documents", type_="foreignkey")
    op.drop_index("ix_documents_vendor_id", table_name="documents")
    op.drop_column("documents", "vendor_id")

    op.drop_index("ix_vendors_project_id", table_name="vendors")
    op.drop_table("vendors")
