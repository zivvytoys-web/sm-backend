"""Add metadata columns used by the media asset model."""
from alembic import op
import sqlalchemy as sa

revision = "2026_10_15_0006"
down_revision = "2026_10_15_0005"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("media_assets")}
    additions = {
        "title": sa.Column("title", sa.String(255), nullable=True),
        "description": sa.Column("description", sa.String(1000), nullable=True),
        "alt_text": sa.Column("alt_text", sa.String(500), nullable=True),
        "tags": sa.Column("tags", sa.JSON(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("media_assets", column)


def downgrade():
    pass
