"""persona update: typos & uncertain-name confirmation rule

Re-applies the current givi_system_prompt.md to auto-seeded prompts that don't
yet contain the TYPOS block. Customized prompts are not touched.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-13 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
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
            "OR (system_prompt LIKE '%Інженер Андрон%' AND system_prompt NOT LIKE '%TYPOS AND UNCERTAIN NAMES%'))"
        ),
        {"p": prompt},
    )


def downgrade() -> None:
    pass
