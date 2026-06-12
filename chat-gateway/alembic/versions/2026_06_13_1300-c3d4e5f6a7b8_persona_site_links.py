"""persona update: official site + link logic

Re-applies the current givi_system_prompt.md to tenants still on a previous
auto-seeded persona (markers: waterfall-era prompt OR agent persona without
the OFFICIAL SITE block). Customized prompts are not touched.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-13 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
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
            "UPDATE bot_settings SET system_prompt = :p "
            "WHERE (system_prompt LIKE '%Waterfall Search%' "
            "OR (system_prompt LIKE '%Інженер Андрон%' AND system_prompt NOT LIKE '%OFFICIAL SITE%'))"
        ),
        {"p": prompt},
    )


def downgrade() -> None:
    pass
