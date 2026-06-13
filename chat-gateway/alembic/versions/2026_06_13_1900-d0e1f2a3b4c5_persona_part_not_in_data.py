"""persona update: part-not-in-data -> google market price + our labour

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-13 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
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
            "OR (system_prompt LIKE '%Інженер Андрон%' AND system_prompt NOT LIKE '%PART NOT IN OUR DATA%'))"
        ),
        {"p": prompt},
    )


def downgrade() -> None:
    pass
