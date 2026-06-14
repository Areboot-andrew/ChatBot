"""route-owned llm sessions

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-15 05:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BACKUP_KEY = "_route_owned_sessions_removed_global_prompts"
REMOVED_KEYS = ("lean_query_prompt", "lean_validator_prompt")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = {key: meta.get(key) for key in REMOVED_KEYS}
        for key in REMOVED_KEYS:
            meta.pop(key, None)
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
        for key in REMOVED_KEYS:
            if backup.get(key) is not None:
                meta[key] = backup[key]
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})
