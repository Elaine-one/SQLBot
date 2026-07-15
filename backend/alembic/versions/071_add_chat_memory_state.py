"""071_add_chat_memory_state

Add memory_state JSON column to chat table for Agent cross-turn persistence.

Revision ID: 071a1b2c3d4e6
Revises: 070a1b2c3d4e5
Create Date: 2026-07-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '071a1b2c3d4e6'
down_revision = '070a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'chat',
        sa.Column('memory_state', sa.JSON(), nullable=True,
                  comment='Agent cross-turn memory state (queries, charts, explored tables, conversation summary)')
    )


def downgrade():
    op.drop_column('chat', 'memory_state')
