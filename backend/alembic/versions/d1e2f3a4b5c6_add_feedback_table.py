"""add feedback table

新增回复反馈表:用户在回复下方点 👍 / 👎 时记录评价,👎 可带问题分类与详情。
不参与 agent 上下文,仅供后续人工排查。

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-09-26 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c0d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('message_id', sa.String(length=64), nullable=True),
        sa.Column('rating', sa.String(length=10), nullable=False),
        sa.Column('categories', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_feedback_user_id', 'feedback', ['user_id'])
    op.create_index('idx_feedback_user_message', 'feedback', ['user_id', 'message_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_feedback_user_message', table_name='feedback')
    op.drop_index('ix_feedback_user_id', table_name='feedback')
    op.drop_table('feedback')
