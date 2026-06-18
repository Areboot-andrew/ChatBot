"""Refresh logical prompts and LM Studio host.

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-06-18 15:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import (
    DEFAULT_UNIVERSAL_PERSONA,
    LEAN_ANSWER_PROMPT,
    LEAN_CONDUCT_PROMPT,
    LEAN_CONTROLLER_PROMPT,
    LEAN_WARNING_PROMPT,
    ROUTE_PROMPTS,
)


revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


OLD_LM_URLS = {
    "http://192.168.1.84:1234/v1",
    "http://192.168.1.84:1234",
}
NEW_LM_URL = "http://192.168.1.85:1234/v1"


def _backup_once(meta: dict, key: str, value):
    backups = dict(meta.get("_backup_2026_06_18_logical_prompts") or {})
    if key not in backups:
        backups[key] = value
    meta["_backup_2026_06_18_logical_prompts"] = backups


def upgrade() -> None:
    conn = op.get_bind()

    settings_rows = conn.execute(
        sa.text("SELECT id, system_prompt, meta FROM bot_settings")
    ).mappings().all()
    for row in settings_rows:
        meta = dict(row["meta"] or {})
        _backup_once(meta, "system_prompt", row["system_prompt"])
        for key in (
            "lean_controller_prompt",
            "lean_answer_prompt",
            "lean_conduct_prompt",
            "lean_warning_prompt",
            "llm_base_url",
        ):
            _backup_once(meta, key, meta.get(key))

        # Existing tenants get the same clean prompt set as new tenants. This is
        # intentional: these prompts are engine control text, not tenant content.
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["lean_conduct_prompt"] = LEAN_CONDUCT_PROMPT
        meta["lean_warning_prompt"] = LEAN_WARNING_PROMPT
        if str(meta.get("llm_base_url") or "").strip() in OLD_LM_URLS:
            meta["llm_base_url"] = NEW_LM_URL

        conn.execute(
            sa.text(
                """
                UPDATE bot_settings
                SET system_prompt = :system_prompt,
                    meta = CAST(:meta AS jsonb)
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "system_prompt": DEFAULT_UNIVERSAL_PERSONA,
                "meta": json.dumps(meta, ensure_ascii=False),
            },
        )

    route_rows = conn.execute(
        sa.text("SELECT id, code, meta FROM knowledge_types")
    ).mappings().all()
    for row in route_rows:
        defaults = ROUTE_PROMPTS.get(str(row["code"]))
        if not defaults:
            continue
        meta = dict(row["meta"] or {})
        _backup_once(meta, f"route:{row['code']}", {
            "tool_name": meta.get("tool_name"),
            "source_description": meta.get("source_description"),
            "query_prompt": meta.get("query_prompt"),
            "result_validation_prompt": meta.get("result_validation_prompt"),
        })
        meta.update(defaults)
        conn.execute(
            sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get("_backup_2026_06_18_logical_prompts") or {})
        values = {}
        if "system_prompt" in backups and backups["system_prompt"] is not None:
            values["system_prompt"] = backups["system_prompt"]
        for key in (
            "lean_controller_prompt",
            "lean_answer_prompt",
            "lean_conduct_prompt",
            "lean_warning_prompt",
            "llm_base_url",
        ):
            if key in backups:
                meta[key] = backups[key]
        meta.pop("_backup_2026_06_18_logical_prompts", None)
        if values:
            conn.execute(
                sa.text("UPDATE bot_settings SET system_prompt = :system_prompt WHERE id = :id"),
                {"id": row["id"], "system_prompt": values["system_prompt"]},
            )
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )

    rows = conn.execute(sa.text("SELECT id, code, meta FROM knowledge_types")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get("_backup_2026_06_18_logical_prompts") or {})
        old = backups.get(f"route:{row['code']}")
        if isinstance(old, dict):
            for key, value in old.items():
                meta[key] = value
            meta.pop("_backup_2026_06_18_logical_prompts", None)
            conn.execute(
                sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
                {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
            )
