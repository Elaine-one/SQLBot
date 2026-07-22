"""add execution_log column to chat_record

Revision ID: 072
Revises: 071
Create Date: 2026-07-16

Agent engine stores structured execution metadata (iterations, duration,
token usage, per-tool timing) in this JSON column.  Replaces the old
chat_log table for agent-produced records.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '072a1b2c3d4e5'
down_revision: Union[str, None] = '071a1b2c3d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_record ADD COLUMN IF NOT EXISTS execution_log JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE chat_record DROP COLUMN IF EXISTS execution_log")
