"""drop web_search flag from conversations

联网工具(web_search / web_fetch)改为常驻工具集,由模型自行判断是否联网,
不再保留会话级开关,故删除该列。

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-09-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('conversations', 'web_search')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'conversations',
        sa.Column('web_search', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
