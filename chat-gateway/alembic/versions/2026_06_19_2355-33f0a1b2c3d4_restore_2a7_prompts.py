"""Restore prompt set after rollback to 2a7 behavior.

Revision ID: 33f0a1b2c3d4
Revises: 32f0a1b2c3d4
Create Date: 2026-06-19 23:55:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import (
    DEFAULT_UNIVERSAL_PERSONA,
    LEAN_ANSWER_PROMPT,
    LEAN_CONDUCT_PROMPT,
    LEAN_CONTROLLER_PROMPT,
    LEAN_WARNING_PROMPT,
)


revision = "33f0a1b2c3d4"
down_revision = "32f0a1b2c3d4"
branch_labels = None
depends_on = None


def _texno_persona() -> str | None:
    for path in (
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def upgrade() -> None:
    conn = op.get_bind()
    texno_persona = _texno_persona()
    rows = conn.execute(sa.text("SELECT id, system_prompt, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["lean_conduct_prompt"] = LEAN_CONDUCT_PROMPT
        meta["lean_warning_prompt"] = LEAN_WARNING_PROMPT
        meta["agent_max_iterations"] = "1"

        current_prompt = str(row["system_prompt"] or "")
        values = {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)}
        should_restore_texno = (
            texno_persona
            and (
                "Інженер Андрон" in current_prompt
                or "texno.plus" in current_prompt
                or "GENERAL SERVICE TALK" in current_prompt
            )
        )
        should_restore_universal = "You may answer general in-domain advice" in current_prompt

        if should_restore_texno:
            values["system_prompt"] = texno_persona
        elif should_restore_universal:
            values["system_prompt"] = DEFAULT_UNIVERSAL_PERSONA

        if "system_prompt" in values:
            conn.execute(
                sa.text(
                    "UPDATE bot_settings "
                    "SET system_prompt = :system_prompt, meta = CAST(:meta AS jsonb) "
                    "WHERE id = :id"
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
