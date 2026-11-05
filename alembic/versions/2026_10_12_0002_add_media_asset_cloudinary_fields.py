"""add cloudinary fields public_id and user_id to media_assets

Revision ID: 2026_10_12_0002
Revises: 2026_10_12_0001
Create Date: 2026-10-12 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2026_10_12_0002'
down_revision = '2026_10_12_0001'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('media_assets')]

    if "public_id" not in existing_columns:
        op.add_column('media_assets', sa.Column('public_id', sa.String(length=255), nullable=True))
        op.create_index(op.f('ix_media_assets_public_id'), 'media_assets', ['public_id'], unique=False)

    if "user_id" not in existing_columns:
        op.add_column('media_assets', sa.Column('user_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_media_assets_user_id',
            'media_assets', 'users',
            ['user_id'], ['id'],
            ondelete='SET NULL'
        )
        op.create_index(op.f('ix_media_assets_user_id'), 'media_assets', ['user_id'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('media_assets')]

    if "user_id" in existing_columns:
        op.drop_constraint('fk_media_assets_user_id', 'media_assets', type_='foreignkey')
        op.drop_index(op.f('ix_media_assets_user_id'), table_name='media_assets')
        op.drop_column('media_assets', 'user_id')

    if "public_id" in existing_columns:
        op.drop_index(op.f('ix_media_assets_public_id'), table_name='media_assets')
        op.drop_column('media_assets', 'public_id')
