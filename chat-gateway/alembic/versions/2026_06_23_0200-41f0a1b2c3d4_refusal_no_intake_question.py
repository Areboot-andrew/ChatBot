"""Answer prompt: a refusal is complete — no intake question after declining.

Reloads bot_settings.meta.lean_answer_prompt. Fixes the trace where the bot
declined ("газонокосарки не ремонтуємо") but then added "Що саме у вас зламалося?"
— pointless for something we don't take. Now the refusal is a short, polite,
slightly warm complete answer with no follow-up intake question.

Revision ID: 41f0a1b2c3d4
Revises: 40f0a1b2c3d4
Create Date: 2026-06-23 02:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT


revision = "41f0a1b2c3d4"
down_revision = "40f0a1b2c3d4"
branch_labels = None
depends_on = None

BACKUP_KEY = "_backup_2026_06_23_refusal_no_intake"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = meta.get("lean_answer_prompt")
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if BACKUP_KEY in meta:
            prev = meta.pop(BACKUP_KEY)
            if prev is not None:
                meta["lean_answer_prompt"] = prev
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )
