"""Phrase catalog prices as orientation, not fixed quotes.

Revision ID: 27f0a1b2c3d4
Revises: 26f0a1b2c3d4
Create Date: 2026-06-19 21:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT, ROUTE_PROMPTS


revision = "27f0a1b2c3d4"
down_revision = "26f0a1b2c3d4"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_orient_prices_not_fixed_quotes"


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
        _backup_once(meta, "lean_answer_prompt", meta.get("lean_answer_prompt"))
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )

    rows = conn.execute(sa.text("SELECT id, code, meta FROM knowledge_types")).mappings().all()
    for row in rows:
        defaults = ROUTE_PROMPTS.get(str(row["code"]))
        if not defaults:
            continue
        meta = dict(row["meta"] or {})
        _backup_once(
            meta,
            f"route:{row['code']}",
            {
                "source_description": meta.get("source_description"),
                "query_prompt": meta.get("query_prompt"),
                "result_validation_prompt": meta.get("result_validation_prompt"),
            },
        )
        for key in ("source_description", "query_prompt", "result_validation_prompt"):
            meta[key] = defaults.get(key, meta.get(key))
        conn.execute(
            sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )


def downgrade() -> None:
    conn = op.get_bind()

    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        if "lean_answer_prompt" in backups:
            meta["lean_answer_prompt"] = backups["lean_answer_prompt"]
        meta.pop(BACKUP_KEY, None)
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )

    rows = conn.execute(sa.text("SELECT id, code, meta FROM knowledge_types")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        old = backups.get(f"route:{row['code']}")
        if isinstance(old, dict):
            for key, value in old.items():
                meta[key] = value
        meta.pop(BACKUP_KEY, None)
        conn.execute(
            sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )
