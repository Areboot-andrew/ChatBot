"""qa validation accepts general capability ('так ремонтуємо') for capability questions

Trace: client asked «бетономішалки робите?», qa search returned «що ви ремонтуємо
-> так ремонтуємо», but the validator rejected it (relevant:false) because the
exact item name was absent. Now a general capability statement counts as
relevant for capability questions. Updates the qa route result_validation_prompt.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-06-15 16:00:00.000000
"""
from typing import Sequence, Union
import json
from alembic import op
import sqlalchemy as sa

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import ROUTE_PROMPTS
    val = ROUTE_PROMPTS["qa"]["result_validation_prompt"]
    rows = conn.execute(sa.text("SELECT id, meta FROM knowledge_types WHERE code = 'qa'")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        meta["result_validation_prompt"] = val
        conn.execute(sa.text("UPDATE knowledge_types SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    pass
