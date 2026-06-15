"""route capability/symptom/cause questions to the FAQ route (catalog -> qa fallback)

The FAQ (qa) base documents whether items are repaired, symptoms and causes, but
the controller sent 'do you repair X' to the catalog only; when the catalog had
no row, the qa answer was never tried. Broadens the qa route source/triggers and
adds the catalog->qa fallback rule to the controller prompt.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-06-15 11:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_QA_TRIGGERS = [
    "гарантія", "умови", "як відбувається", "терміни", "правила",
    "чи ремонтуєте", "що ви ремонтуєте", "чи робите", "діагностика",
    "симптом", "причина", "чому", "як працює ремонт",
]


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import LEAN_CONTROLLER_PROMPT, ROUTE_PROMPTS

    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        conn.execute(sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})

    qa_src = ROUTE_PROMPTS["qa"]["source_description"]
    kt = conn.execute(sa.text("SELECT id, meta FROM knowledge_types WHERE code = 'qa'")).mappings().all()
    for r in kt:
        meta = dict(r["meta"] or {})
        meta["source_description"] = qa_src
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:m AS jsonb), intent_patterns = CAST(:p AS jsonb) WHERE id = :id"
        ), {"m": json.dumps(meta, ensure_ascii=False),
            "p": json.dumps(_QA_TRIGGERS, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    pass
