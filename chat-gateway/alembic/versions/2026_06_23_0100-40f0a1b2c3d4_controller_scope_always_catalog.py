"""Controller: scope is ALWAYS catalog; web_search only for external spec.

Reloads bot_settings.meta.lean_controller_prompt. Fixes the trace where "хочу
привезти велосипед" was routed to web_search, which found other bike shops online
and made the bot claim WE repair bicycles. Now "do you do/repair/bring X" always
goes to catalog (returns not-found -> decline), and web_search is only for an
external spec of an in-scope item, never to decide whether we service something.

Revision ID: 40f0a1b2c3d4
Revises: 39f0a1b2c3d4
Create Date: 2026-06-23 01:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_CONTROLLER_PROMPT


revision = "40f0a1b2c3d4"
down_revision = "39f0a1b2c3d4"
branch_labels = None
depends_on = None

BACKUP_KEY = "_backup_2026_06_23_controller_scope"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = meta.get("lean_controller_prompt")
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
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
                meta["lean_controller_prompt"] = prev
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )
