"""add the optional creator reference to brand profiles

Revision ID: 2026_10_15_0004
Revises: 2026_10_15_0003
Create Date: 2026-10-15 15:35:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_10_15_0004"
down_revision = "2026_10_15_0003"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    column_names = {column["name"] for column in inspector.get_columns("brand_profiles")}

    if "user_id" not in column_names:
        op.add_column("brand_profiles", sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_brand_profiles_user_id",
            "brand_profiles",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_brand_profiles_user_id", "brand_profiles", ["user_id"], unique=False)


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    column_names = {column["name"] for column in inspector.get_columns("brand_profiles")}

    if "user_id" in column_names:
        op.drop_index("ix_brand_profiles_user_id", table_name="brand_profiles")
        op.drop_constraint("fk_brand_profiles_user_id", "brand_profiles", type_="foreignkey")
        op.drop_column("brand_profiles", "user_id")
