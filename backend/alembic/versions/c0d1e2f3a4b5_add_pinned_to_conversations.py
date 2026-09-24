"""add pinned to conversations

会话置顶:左侧会话列表右键/⋯菜单可置顶,列表排序改为「置顶优先 + 更新时间倒序」。

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-09-22 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, Sequence[str], None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(name: str) -> bool:
    """检查列是否已存在:兼容早期手工建列 / 迁移顺序调整导致的 schema 漂移。"""
    bind = op.get_bind()
    return name in {c["name"] for c in sa.inspect(bind).get_columns('conversations')}


def upgrade() -> None:
    """Upgrade schema."""
    if _has_column('pinned'):
        return
    op.add_column(
        'conversations',
        sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_column('pinned'):
        return
    op.drop_column('conversations', 'pinned')
