"""replace waterfall-era system prompt with agent-compatible persona

The old prompt told the model "You DO NOT need to output JSON, facts are
already given" (waterfall architecture) — this directly conflicts with the
agent engine's ROUTER_DECISION JSON protocol. Replace it with the updated
persona, but ONLY where the old waterfall marker is present, so customized
tenant prompts are never overwritten.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-13 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROMPT_PATH = "/app/app/givi_system_prompt.md"


def upgrade() -> None:
    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            prompt = f.read()
    except OSError:
        # Running outside the container (no prompt file) — nothing to do.
        return
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE bot_settings SET system_prompt = :p WHERE system_prompt LIKE '%Waterfall Search%'"),
        {"p": prompt},
    )


def downgrade() -> None:
    pass
