"""seed temporary Serper (Google) key for existing tenants where empty

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-13 21:00:00.000000

Temporary key — replace with your own in Settings -> Пошук в інтернеті.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE bot_settings "
        "SET meta = (CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END) "
        "         || jsonb_build_object('serper_api_key', '2d030163fbd463059411ab1c1f7ba67220a8510d') "
        "WHERE COALESCE(meta->>'serper_api_key', '') = ''"
    ))


def downgrade() -> None:
    pass
