"""Add the creator reference used by BrandProfile."""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_15_0005"
down_revision = "2026_10_15_0004"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("brand_profiles")}
    if "user_id" not in columns:
        op.add_column("brand_profiles", sa.Column("user_id", sa.Integer(), nullable=True))
        op.create_index("ix_brand_profiles_user_id", "brand_profiles", ["user_id"])


def downgrade():
    op.drop_index("ix_brand_profiles_user_id", table_name="brand_profiles")
    op.drop_column("brand_profiles", "user_id")
