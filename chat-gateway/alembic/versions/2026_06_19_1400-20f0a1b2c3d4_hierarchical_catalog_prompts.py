"""Hierarchical catalog content map prompts.

Revision ID: 20f0a1b2c3d4
Revises: 19f0a1b2c3d4
Create Date: 2026-06-19 14:00:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.prompt_defaults import (
    LEAN_ANSWER_PROMPT,
    LEAN_CONDUCT_PROMPT,
    LEAN_CONTROLLER_PROMPT,
    ROUTE_PROMPTS,
)


revision = "20f0a1b2c3d4"
down_revision = "19f0a1b2c3d4"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_hierarchical_catalog_prompts"


def _load_texno_persona() -> str | None:
    for path in (
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ):
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def _backup_once(meta: dict, key: str, value) -> None:
    backups = dict(meta.get(BACKUP_KEY) or {})
    if key not in backups:
        backups[key] = value
    meta[BACKUP_KEY] = backups


def upgrade() -> None:
    op.add_column(
        "service_prices",
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    conn = op.get_bind()
    texno_persona = _load_texno_persona()

    rows = conn.execute(
        sa.text(
            """
            SELECT bs.id, bs.system_prompt, bs.meta, t.name AS tenant_name
            FROM bot_settings bs
            LEFT JOIN tenants t ON t.id = bs.tenant_id
            """
        )
    ).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        _backup_once(meta, "system_prompt", row["system_prompt"])
        for key in ("lean_controller_prompt", "lean_answer_prompt", "lean_conduct_prompt"):
            _backup_once(meta, key, meta.get(key))

        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["lean_conduct_prompt"] = LEAN_CONDUCT_PROMPT

        tenant_name = str(row["tenant_name"] or "").lower()
        system_prompt = row["system_prompt"]
        if texno_persona and (
            "texno" in tenant_name
            or "техно" in tenant_name
            or "техноплюс" in tenant_name
        ):
            system_prompt = texno_persona

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
                "system_prompt": system_prompt,
                "meta": json.dumps(meta, ensure_ascii=False),
            },
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
                "tool_name": meta.get("tool_name"),
            },
        )
        for key in ("source_description", "query_prompt", "result_validation_prompt", "tool_name"):
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
        system_prompt = backups.get("system_prompt")
        if system_prompt is not None:
            conn.execute(
                sa.text("UPDATE bot_settings SET system_prompt = :system_prompt WHERE id = :id"),
                {"id": row["id"], "system_prompt": system_prompt},
            )
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
        if not isinstance(old, dict):
            continue
        for key, value in old.items():
            meta[key] = value
        meta.pop(BACKUP_KEY, None)
        conn.execute(
            sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )

    op.drop_column("service_prices", "meta")
