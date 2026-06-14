"""reject empty or sentinel final answers

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-06-14 21:30:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import DEFAULT_ANSWER_STYLE

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
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
        meta["answer_style"] = DEFAULT_ANSWER_STYLE
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)})


def downgrade() -> None:
    pass
