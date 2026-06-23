"""Two-step part pricing: catalog work price + supplier part price.

Reloads lean_controller_prompt and sets agent_max_iterations=2 so the controller
can, for a model-specific part replacement, do step 1 = catalog (work price) and
step 2 = external_price (part market price from supplier sites, e.g. gsm-forsage).
The answer then combines work + part as an orientation. Only triggers when a
concrete model is given; simple turns still stop at one route.

Revision ID: 42f0a1b2c3d4
Revises: 41f0a1b2c3d4
Create Date: 2026-06-23 03:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import LEAN_CONTROLLER_PROMPT


revision = "42f0a1b2c3d4"
down_revision = "41f0a1b2c3d4"
branch_labels = None
depends_on = None

BACKUP_KEY = "_backup_2026_06_23_two_step"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        backups.setdefault("lean_controller_prompt", meta.get("lean_controller_prompt"))
        backups.setdefault("agent_max_iterations", meta.get("agent_max_iterations"))
        meta[BACKUP_KEY] = backups
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["agent_max_iterations"] = "2"
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backups = dict(meta.get(BACKUP_KEY) or {})
        if "lean_controller_prompt" in backups and backups["lean_controller_prompt"] is not None:
            meta["lean_controller_prompt"] = backups["lean_controller_prompt"]
        if backups.get("agent_max_iterations") is not None:
            meta["agent_max_iterations"] = backups["agent_max_iterations"]
        meta.pop(BACKUP_KEY, None)
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )
