"""Answer prompt: give catalog orientation prices, ask type not model.

Reloads bot_settings.meta.lean_answer_prompt from the updated LEAN_ANSWER_PROMPT:
- unvalidated catalog rows WITH prices may be given as an orientation (they are
  real rows from our catalog) — stops the bot from hiding prices it already has;
- for services, ask the TYPE (ріжкова/автоматична/капсульна), not the brand/model;
- ignore cross-category noise rows that merely share a word.

Backs up the previous value for downgrade.

Revision ID: 38f0a1b2c3d4
Revises: 37f0a1b2c3d4
Create Date: 2026-06-21 04:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT


revision = "38f0a1b2c3d4"
down_revision = "37f0a1b2c3d4"
branch_labels = None
depends_on = None

BACKUP_KEY = "_backup_2026_06_21_answer_prices"


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
