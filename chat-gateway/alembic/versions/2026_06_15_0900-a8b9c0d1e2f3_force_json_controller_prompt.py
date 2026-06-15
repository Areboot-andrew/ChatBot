"""force-update lean_controller_prompt to the JSON-only router prompt

The seeded controller prompt was not forceful enough, so the controller often
replied to the client in prose instead of returning a JSON routing decision —
routes never fired and the model invented business facts (wrong city/hours).
This overwrites lean_controller_prompt for all tenants with the strict JSON-only
version. answer/conduct/warning prompts are left as-is.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-15 09:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import LEAN_CONTROLLER_PROMPT
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        conn.execute(sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    pass
