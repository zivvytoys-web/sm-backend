"""Add quality and duplicate-prevention fields to content tables."""

from alembic import op
import sqlalchemy as sa


revision = "2026_10_23_0007"
down_revision = "2026_10_15_0006"
branch_labels = None
depends_on = None


def _add_missing(table_name, columns):
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}
    for name, column in columns.items():
        if name not in existing:
            op.add_column(table_name, column)


def upgrade():
    _add_missing(
        "contents",
        {
            "sha256_hash": sa.Column("sha256_hash", sa.String(64), nullable=True),
            "validation_status": sa.Column("validation_status", sa.String(32), nullable=False, server_default="PENDING"),
            "quality_score": sa.Column("quality_score", sa.Float(), nullable=False, server_default="100"),
            "is_duplicate": sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
        },
    )
    _add_missing(
        "media_assets",
        {
            "sha256_hash": sa.Column("sha256_hash", sa.String(64), nullable=True),
            "perceptual_hash": sa.Column("perceptual_hash", sa.String(64), nullable=True),
            "validation_status": sa.Column("validation_status", sa.String(32), nullable=False, server_default="PENDING"),
            "quality_score": sa.Column("quality_score", sa.Float(), nullable=False, server_default="100"),
            "is_duplicate": sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
            "duplicate_of_id": sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        },
    )


def downgrade():
    pass
