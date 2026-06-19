"""Prefer scope verification before contact/drop-off instructions.

Revision ID: 22f0a1b2c3d4
Revises: 21f0a1b2c3d4
Create Date: 2026-06-19 16:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT, LEAN_CONTROLLER_PROMPT


revision = "22f0a1b2c3d4"
down_revision = "21f0a1b2c3d4"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_scope_before_contacts_prompts"


def _backup_once(meta: dict, key: str, value) -> None:
    backups = dict(meta.get(BACKUP_KEY) or {})
    if key not in backups:
        backups[key] = value
    meta[BACKUP_KEY] = backups


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        _backup_once(meta, "lean_controller_prompt", meta.get("lean_controller_prompt"))
        _backup_once(meta, "lean_answer_prompt", meta.get("lean_answer_prompt"))
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        for key in ("lean_controller_prompt", "lean_answer_prompt"):
            if key in backups:
                meta[key] = backups[key]
        meta.pop(BACKUP_KEY, None)
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )
