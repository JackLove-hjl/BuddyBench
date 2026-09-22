"""add users table and bind conversations/providers to user

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    注意:providers.name 的全局唯一约束改为 (user_id, name) 联合唯一。
    """
    # users 表
    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_username', 'users', ['username'], unique=True)

    # conversations 加 user_id
    op.add_column('conversations', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])

    # providers 加 user_id;删除旧全局唯一约束,改联合唯一
    op.add_column('providers', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_index('ix_providers_user_id', 'providers', ['user_id'])
    op.drop_constraint('providers_name_key', 'providers', type_='unique')
    op.create_index('ix_providers_user_name', 'providers', ['user_id', 'name'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_providers_user_name', table_name='providers')
    op.create_unique_constraint('providers_name_key', 'providers', ['name'])
    op.drop_index('ix_providers_user_id', table_name='providers')
    op.drop_column('providers', 'user_id')
    op.drop_index('ix_conversations_user_id', table_name='conversations')
    op.drop_column('conversations', 'user_id')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
