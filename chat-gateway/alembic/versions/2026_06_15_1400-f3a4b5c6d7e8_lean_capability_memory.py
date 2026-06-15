"""simpler capability check + push updated controller/answer prompts

Removed the over-strict 'never repeat a route' rule (a different item is a valid
new search); kept confident per-item verification across catalog->qa. Pairs with
the engine change that persists verified facts in chat memory.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-15 14:00:00.000000
"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
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
