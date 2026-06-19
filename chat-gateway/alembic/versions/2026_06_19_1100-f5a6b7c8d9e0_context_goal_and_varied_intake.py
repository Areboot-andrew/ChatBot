"""Use active chat goal for routing and vary intake wording.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-06-19 11:00:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT, LEAN_CONTROLLER_PROMPT


revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_context_goal_varied_intake"


def _load_texno_persona() -> str | None:
    for path in (
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ):
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def upgrade() -> None:
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
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = {
                "system_prompt": row["system_prompt"],
                "lean_controller_prompt": meta.get("lean_controller_prompt"),
                "lean_answer_prompt": meta.get("lean_answer_prompt"),
            }
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT

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


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backup = meta.get(BACKUP_KEY)
        if not isinstance(backup, dict):
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
                "system_prompt": backup.get("system_prompt", ""),
                "meta": json.dumps(
                    {
                        **{k: v for k, v in meta.items() if k != BACKUP_KEY},
                        "lean_controller_prompt": backup.get("lean_controller_prompt"),
                        "lean_answer_prompt": backup.get("lean_answer_prompt"),
                    },
                    ensure_ascii=False,
                ),
            },
        )
