"""Guard scope questions when controller fails and narrow texno persona.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-06-19 12:00:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op


revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_scope_fallback_persona"


def _load_texno_persona() -> str | None:
    for path in (
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ):
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def upgrade() -> None:
    persona = _load_texno_persona()
    if not persona:
        return
    conn = op.get_bind()
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
        tenant_name = str(row["tenant_name"] or "").lower()
        if not ("texno" in tenant_name or "техно" in tenant_name or "техноплюс" in tenant_name):
            continue
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = {"system_prompt": row["system_prompt"]}
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
                "system_prompt": persona,
                "meta": json.dumps(meta, ensure_ascii=False),
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backup = meta.get(BACKUP_KEY)
        if not isinstance(backup, dict):
            continue
        system_prompt = backup.get("system_prompt")
        meta.pop(BACKUP_KEY, None)
        if system_prompt is None:
            continue
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
