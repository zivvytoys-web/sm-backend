"""add user_organizations table and multi business support

Revision ID: 2026_10_12_0001
Revises: 
Create Date: 2026-10-12 10:25:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '2026_10_12_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create user_organizations table if it doesn't already exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    
    if "user_organizations" not in tables:
        op.create_table(
            'user_organizations',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('role', sa.Enum('SUPER_ADMIN', 'ADMIN', 'MANAGER', 'CREATOR', 'VIEWER', name='userrole'), nullable=False),
            sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_user_organizations_id'), 'user_organizations', ['id'], unique=False)
        op.create_index(op.f('ix_user_organizations_organization_id'), 'user_organizations', ['organization_id'], unique=False)
        op.create_index(op.f('ix_user_organizations_user_id'), 'user_organizations', ['user_id'], unique=False)


def downgrade():
    op.drop_table('user_organizations')
