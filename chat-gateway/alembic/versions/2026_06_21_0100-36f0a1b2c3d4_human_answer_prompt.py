"""Update answer prompt to the human-master refusal style.

Rewrites bot_settings.meta.lean_answer_prompt to the shorter style:
- refuse like a master ("питав майстра — зараз такого не беремо"), never expose a
  catalog/price-list/database, never "немає в прайсі/базі";
- ask for the device model ONLY when a spare-part / external-price search needs it;
- graded refusals (type not handled / not taken / can take a look if minor).

The previous value is backed up in meta so downgrade restores it.

Revision ID: 36f0a1b2c3d4
Revises: 35f0a1b2c3d4
Create Date: 2026-06-21 01:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT


revision = "36f0a1b2c3d4"
down_revision = "35f0a1b2c3d4"
branch_labels = None
depends_on = None

BACKUP_KEY = "_backup_2026_06_21_answer_prompt"


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
