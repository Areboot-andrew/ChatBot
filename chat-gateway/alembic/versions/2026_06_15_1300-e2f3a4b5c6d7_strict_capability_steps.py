"""stricter capability steps: no repeat route, catalog->qa, no assumed capability

Trace: after catalog returned nothing the controller re-picked catalog (hit the
duplicate guard, stopped) and the answer assumed the item is repaired. Force-
updates lean_controller_prompt (no repeat route + explicit catalog->qa steps)
and lean_answer_prompt (capability only from facts).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-15 13:00:00.000000
"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import LEAN_CONTROLLER_PROMPT, LEAN_ANSWER_PROMPT
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        conn.execute(sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    pass
