"""add workspace-owned OAuth developer configurations

Revision ID: 2026_10_15_0003
Revises: 2026_10_12_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_15_0003"
down_revision = "2026_10_12_0002"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if "workspace_integrations" in sa.inspect(conn).get_table_names():
        return
    op.create_table(
        "workspace_integrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret_encrypted", sa.String(length=2048), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False, server_default=sa.text("('{}')")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("verification_status", sa.String(length=32), nullable=False, server_default="untested"),
        sa.Column("verification_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "provider", name="uq_workspace_provider"),
    )
    op.create_index("ix_workspace_integrations_organization_id", "workspace_integrations", ["organization_id"])
    op.create_index("ix_workspace_integrations_provider", "workspace_integrations", ["provider"])


def downgrade():
    op.drop_table("workspace_integrations")
