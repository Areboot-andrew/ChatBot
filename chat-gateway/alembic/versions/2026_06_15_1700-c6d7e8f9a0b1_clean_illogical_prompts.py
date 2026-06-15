"""remove contradictory/illogical inserts from controller + answer prompts

The answer prompt told the model both 'never convert a missing result into
confirmation OR refusal' AND 'if FAQ shows not handled, say so' — contradictory,
so the model froze/looped. The controller forced a rigid catalog->qa 2-step the
model could not hold. Rewrote both: capability questions route straight to qa
(the FAQ owns 'what we repair'), prices to catalog; answer handles capability in
one clear rule (fact confirms -> yes; fact excludes -> no; nothing -> persona).

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-06-15 17:00:00.000000
"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa

revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"
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
