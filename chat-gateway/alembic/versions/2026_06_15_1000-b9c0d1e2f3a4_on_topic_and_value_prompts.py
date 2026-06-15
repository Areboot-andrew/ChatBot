"""push on-topic answer/conduct prompts + business_info value-not-key validation

From a live trace: the bot tolerated off-topic gibberish (looping 'що не працює?'),
conduct didn't flag trolling, and the business_info route returned field NAMES
('working_hours') instead of values. This force-updates lean_answer_prompt,
lean_conduct_prompt and the business_info route's result_validation_prompt.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-06-15 10:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import LEAN_ANSWER_PROMPT, LEAN_CONDUCT_PROMPT, ROUTE_PROMPTS

    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["lean_conduct_prompt"] = LEAN_CONDUCT_PROMPT
        conn.execute(sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})

    biz_val = ROUTE_PROMPTS["business_info"]["result_validation_prompt"]
    kt = conn.execute(sa.text("SELECT id, meta FROM knowledge_types WHERE code = 'business_info'")).mappings().all()
    for r in kt:
        meta = dict(r["meta"] or {})
        meta["result_validation_prompt"] = biz_val
        conn.execute(sa.text("UPDATE knowledge_types SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    pass
