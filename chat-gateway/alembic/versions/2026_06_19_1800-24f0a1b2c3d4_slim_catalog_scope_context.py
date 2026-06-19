"""Slim catalog scope context and purge generated item noise.

Revision ID: 24f0a1b2c3d4
Revises: 23f0a1b2c3d4
Create Date: 2026-06-19 18:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import (
    LEAN_ANSWER_PROMPT,
    LEAN_CONDUCT_PROMPT,
    LEAN_CONTROLLER_PROMPT,
    ROUTE_PROMPTS,
)


revision = "24f0a1b2c3d4"
down_revision = "23f0a1b2c3d4"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_slim_catalog_scope_context"


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
        for key in ("lean_controller_prompt", "lean_answer_prompt", "lean_conduct_prompt"):
            _backup_once(meta, key, meta.get(key))
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["lean_conduct_prompt"] = LEAN_CONDUCT_PROMPT
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

    conn.execute(
        sa.text(
            """
            UPDATE service_prices
            SET meta = COALESCE(meta, '{}'::jsonb)
                - 'brand'
                - 'availability'
                - 'characteristics'
                - 'composition'
            WHERE meta IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        for key in ("lean_controller_prompt", "lean_answer_prompt", "lean_conduct_prompt"):
            if key in backups:
                meta[key] = backups[key]
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
