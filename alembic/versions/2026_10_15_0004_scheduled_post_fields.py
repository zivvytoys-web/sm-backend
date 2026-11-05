"""Align scheduled_posts with the current ScheduledPost model.

Revision ID: 2026_10_15_0004
Revises: 2026_10_15_0003
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_15_0004"
down_revision = "2026_10_15_0003"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("scheduled_posts")}

    columns = {
        "title": sa.Column("title", sa.String(255), nullable=True),
        "caption": sa.Column("caption", sa.Text(), nullable=True),
        "hashtags": sa.Column("hashtags", sa.JSON(), nullable=False, server_default=sa.text("('{}')")),
        "internal_tags": sa.Column("internal_tags", sa.JSON(), nullable=False, server_default=sa.text("('{}')")),
        "platform": sa.Column("platform", sa.String(50), nullable=False, server_default="instagram"),
        "post_format": sa.Column("post_format", sa.String(50), nullable=False, server_default="post"),
        "error_message": sa.Column("error_message", sa.Text(), nullable=True),
        "media_asset_id": sa.Column("media_asset_id", sa.Integer(), nullable=True),
        "schedule_rule_id": sa.Column("schedule_rule_id", sa.Integer(), nullable=True),
        "organization_id": sa.Column("organization_id", sa.Integer(), nullable=True),
        "user_id": sa.Column("user_id", sa.Integer(), nullable=True),
    }
    for name, column in columns.items():
        if name not in existing:
            op.add_column("scheduled_posts", column)


def downgrade():
    pass
