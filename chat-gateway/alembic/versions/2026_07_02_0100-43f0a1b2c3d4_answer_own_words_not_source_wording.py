"""Answer prompt: route instructions bind substance, never wording.

Reloads bot_settings.meta.lean_answer_prompt. Fixes the register leak seen in
the vacuum-cleaner trace: the validator's bureaucratic answer_instruction
("не включено до каталогу послуг... містить лише мікрохвильовки, блендери...")
was copied almost verbatim into the client reply. Now the prompt states that
route facts/instructions dictate WHAT to say, never HOW: the model must
rephrase in live speech, never recite category/item lists, and the banned
officialese examples are extended.

Revision ID: 43f0a1b2c3d4
Revises: 42f0a1b2c3d4
Create Date: 2026-07-02 01:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT


revision = "43f0a1b2c3d4"
down_revision = "42f0a1b2c3d4"
branch_labels = None
depends_on = None

BACKUP_KEY = "_backup_2026_07_02_own_words"


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
