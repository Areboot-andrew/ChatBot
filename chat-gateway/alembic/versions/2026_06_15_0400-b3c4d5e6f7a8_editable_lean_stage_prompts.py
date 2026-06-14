"""editable lean stage prompts

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-06-15 04:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKUP_KEY = "_editable_lean_stage_prompts_backup"
PROMPT_KEYS = (
    "lean_controller_prompt",
    "lean_query_prompt",
    "lean_validator_prompt",
    "lean_answer_prompt",
    "lean_conduct_prompt",
    "lean_warning_prompt",
)


def _defaults() -> dict:
    from app.core.prompt_defaults import (
        LEAN_CONTROLLER_PROMPT, LEAN_QUERY_PROMPT, LEAN_VALIDATOR_PROMPT,
        LEAN_ANSWER_PROMPT, LEAN_CONDUCT_PROMPT, LEAN_WARNING_PROMPT,
    )
    return {
        "lean_controller_prompt": LEAN_CONTROLLER_PROMPT,
        "lean_query_prompt": LEAN_QUERY_PROMPT,
        "lean_validator_prompt": LEAN_VALIDATOR_PROMPT,
        "lean_answer_prompt": LEAN_ANSWER_PROMPT,
        "lean_conduct_prompt": LEAN_CONDUCT_PROMPT,
        "lean_warning_prompt": LEAN_WARNING_PROMPT,
    }


def upgrade() -> None:
    conn = op.get_bind()
    defaults = _defaults()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = {key: meta.get(key) for key in PROMPT_KEYS}
        for key, value in defaults.items():
            if not str(meta.get(key) or "").strip():
                meta[key] = value
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, meta FROM bot_settings WHERE meta ? :key"
    ), {"key": BACKUP_KEY}).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backup = meta.pop(BACKUP_KEY)
        for key in PROMPT_KEYS:
            if backup.get(key) is None:
                meta.pop(key, None)
            else:
                meta[key] = backup[key]
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})
