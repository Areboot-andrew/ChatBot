"""persona update: never claim release status from memory

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROMPT_PATH = "/app/app/givi_system_prompt.md"


def upgrade() -> None:
    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            prompt = f.read()
    except OSError:
        return
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE bot_settings SET system_prompt = cast(:p as text) "
            "WHERE (system_prompt LIKE '%Waterfall Search%' "
            "OR (system_prompt LIKE '%Інженер Андрон%' AND system_prompt NOT LIKE '%release dates and new models%'))"
        ),
        {"p": prompt},
    )


def downgrade() -> None:
    pass
