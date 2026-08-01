"""073_seed_expand_thinking_block

Seed chat.expand_thinking_block='true' into sys_arg.

WHY: xpack's compiled arg_manage.so returns a builtin default of 'false'
for chat.expand_thinking_block when no sys_arg row exists. That default
silently suppresses all reasoning SSE emission (executor._emit_reasoning
gates on this value) AND collapses the frontend thinking panel, so the
"thinking process" feature appears broken out of the box. The sys_arg
table created in 035_sys_arg_ddl.py was never seeded, leaving the builtin
'false' as the effective value.

This migration inserts the authoritative 'true' default. The value is
still user-overridable via the UI toggle at
frontend/src/views/system/parameter/index.vue (el-switch bound to
chat.expand_thinking_block), which calls save_parameter_args to upsert
the same row. Idempotent: uses INSERT ... WHERE NOT EXISTS because
sys_arg has no unique constraint on pkey (ON CONFLICT unavailable).

Revision ID: 073a1b2c3d4e5
Revises: 072a1b2c3d4e5
Create Date: 2026-08-01
"""
from alembic import op

# revision identifiers used by Alembic.
revision = '073a1b2c3d4e5'
down_revision = '072a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO sys_arg (pkey, pval, ptype, sort_no) "
        "SELECT 'chat.expand_thinking_block', 'true', 'str', 1 "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM sys_arg WHERE pkey = 'chat.expand_thinking_block'"
        ")"
    )


def downgrade() -> None:
    # Removes the seeded row. Note: if a user changed pval after upgrade,
    # this DELETE also removes their override — that is the honest inverse
    # of a seed insert. Re-running upgrade re-seeds the default.
    op.execute(
        "DELETE FROM sys_arg WHERE pkey = 'chat.expand_thinking_block'"
    )
