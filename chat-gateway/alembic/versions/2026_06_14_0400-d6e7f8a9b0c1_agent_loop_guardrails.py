"""agent loop guardrails and refreshed prompts

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-06-14 04:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import DEFAULT_ANSWER_STYLE, DEFAULT_DECISION_RULES

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, meta, system_prompt FROM bot_settings"
    )).mappings().all()
    for row in rows:
        persona = str(row["system_prompt"] or "")
        if "Інженер Андрон" not in persona and "texno.plus" not in persona:
            continue
        meta = dict(row["meta"] or {})
        meta["agent_decision_rules"] = DEFAULT_DECISION_RULES
        meta["answer_style"] = DEFAULT_ANSWER_STYLE
        meta["agent_max_iterations"] = "3"
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )


def downgrade() -> None:
    pass
