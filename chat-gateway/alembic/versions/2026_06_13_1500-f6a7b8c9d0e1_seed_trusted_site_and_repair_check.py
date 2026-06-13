"""seed texno.plus trusted site for existing tenants

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-13 15:00:00.000000

Note: the repair_check example intent was removed from this migration — its
INSERT...SELECT broke deploys repeatedly and it's only a convenience example
(seed.py creates it for fresh installs). Add it from the panel if needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Set trusted site to texno.plus where empty, only for the texno persona.
    conn.execute(sa.text(
        "UPDATE bot_settings "
        "SET meta = (CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END) "
        "         || jsonb_build_object('fallback_sites', 'texno.plus') "
        "WHERE system_prompt LIKE '%Інженер Андрон%' "
        "AND COALESCE(meta->>'fallback_sites', '') = ''"
    ))


def downgrade() -> None:
    pass
