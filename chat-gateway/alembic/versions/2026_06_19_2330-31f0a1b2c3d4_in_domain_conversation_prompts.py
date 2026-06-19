"""Allow safe in-domain conversation without knowledge routes.

Revision ID: 31f0a1b2c3d4
Revises: 30f0a1b2c3d4
Create Date: 2026-06-19 23:30:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT, LEAN_CONTROLLER_PROMPT


revision = "31f0a1b2c3d4"
down_revision = "30f0a1b2c3d4"
branch_labels = None
depends_on = None


def _persona() -> str | None:
    for path in (
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def upgrade() -> None:
    conn = op.get_bind()
    persona = _persona()
    rows = conn.execute(sa.text("SELECT id, system_prompt, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT

        values = {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)}
        current_prompt = str(row["system_prompt"] or "")
        if persona and ("Інженер Андрон" in current_prompt or "texno.plus" in current_prompt):
            values["system_prompt"] = persona
            conn.execute(
                sa.text(
                    "UPDATE bot_settings SET system_prompt = :system_prompt, meta = CAST(:meta AS jsonb) WHERE id = :id"
                ),
                values,
            )
        else:
            conn.execute(
                sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
                values,
            )


def downgrade() -> None:
    pass
