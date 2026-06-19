"""Force LM Studio host from .84 to .85 in tenant settings.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-19 09:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


OLD_HOST = "192.168.1.84"
NEW_HOST = "192.168.1.85"
BACKUP_KEY = "_backup_2026_06_19_lmstudio_host"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        raw = str(meta.get("llm_base_url") or "")
        if OLD_HOST not in raw:
            continue
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = {"llm_base_url": raw}
        meta["llm_base_url"] = raw.replace(OLD_HOST, NEW_HOST)
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backup = meta.get(BACKUP_KEY)
        if not isinstance(backup, dict):
            continue
        meta["llm_base_url"] = backup.get("llm_base_url", meta.get("llm_base_url"))
        meta.pop(BACKUP_KEY, None)
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )
