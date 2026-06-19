"""Guard scope answers when controller fails.

Revision ID: 28f0a1b2c3d4
Revises: 27f0a1b2c3d4
Create Date: 2026-06-19 22:00:00.000000
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_ANSWER_PROMPT, LEAN_CONTROLLER_PROMPT


revision = "28f0a1b2c3d4"
down_revision = "27f0a1b2c3d4"
branch_labels = None
depends_on = None


BACKUP_KEY = "_backup_2026_06_19_scope_fallback_and_persona_guard"


def _backup_once(meta: dict, key: str, value) -> None:
    backups = dict(meta.get(BACKUP_KEY) or {})
    if key not in backups:
        backups[key] = value
    meta[BACKUP_KEY] = backups


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
        _backup_once(meta, "lean_controller_prompt", meta.get("lean_controller_prompt"))
        _backup_once(meta, "lean_answer_prompt", meta.get("lean_answer_prompt"))
        _backup_once(meta, "controller_structural_fallback", meta.get("controller_structural_fallback"))
        _backup_once(meta, "system_prompt", row["system_prompt"])
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["controller_structural_fallback"] = "1"

        current_prompt = str(row["system_prompt"] or "")
        values = {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)}
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
    conn = op.get_bind()

    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        values = {"id": row["id"]}
        if "lean_controller_prompt" in backups:
            meta["lean_controller_prompt"] = backups["lean_controller_prompt"]
        if "lean_answer_prompt" in backups:
            meta["lean_answer_prompt"] = backups["lean_answer_prompt"]
        if "controller_structural_fallback" in backups:
            meta["controller_structural_fallback"] = backups["controller_structural_fallback"]
        if "system_prompt" in backups:
            values["system_prompt"] = backups["system_prompt"]
        meta.pop(BACKUP_KEY, None)
        values["meta"] = json.dumps(meta, ensure_ascii=False)
        if "system_prompt" in values:
            conn.execute(
                sa.text("UPDATE bot_settings SET system_prompt = :system_prompt, meta = CAST(:meta AS jsonb) WHERE id = :id"),
                values,
            )
        else:
            conn.execute(
                sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
                values,
            )
