"""reseed persona + policy prompts v4 (symptom engagement, human conversation, proactive delivery)

Re-applies the universal instruction prompts from the canonical code sources so
the updated non-interrogating, orient-by-catalog-range logic reaches live
tenants (the earlier reseed migration already ran and won't re-run).

Overwrites: bot_settings.system_prompt + meta policy keys. Route prose and
tenant data are left as-is. Reversible: previous values snapshotted, downgrade
restores them.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-15 00:00:00.000000
"""
from typing import Sequence, Union
from pathlib import Path
import json

from alembic import op
import sqlalchemy as sa

revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BACKUP_KEY = "_prompt_backup_d9e0f1a2b3c4"
_META_PROMPT_KEYS = [
    "agent_decision_rules",
    "intake_policy",
    "conduct_policy",
    "answer_style",
    "parts_instruction",
    "tpl_evaluation_rules",
]


def _load_persona() -> str:
    candidates = [
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError("givi_system_prompt.md not found")


def _defaults() -> dict:
    from app.core.prompt_defaults import (
        DEFAULT_DECISION_RULES, DEFAULT_INTAKE_POLICY, DEFAULT_CONDUCT_POLICY,
        DEFAULT_ANSWER_STYLE, DEFAULT_PARTS_INSTRUCTION, DEFAULT_EVALUATION_RULES,
    )
    return {
        "agent_decision_rules": DEFAULT_DECISION_RULES,
        "intake_policy": DEFAULT_INTAKE_POLICY,
        "conduct_policy": DEFAULT_CONDUCT_POLICY,
        "answer_style": DEFAULT_ANSWER_STYLE,
        "parts_instruction": DEFAULT_PARTS_INSTRUCTION,
        "tpl_evaluation_rules": DEFAULT_EVALUATION_RULES,
    }


def upgrade() -> None:
    conn = op.get_bind()
    persona = _load_persona()
    defaults = _defaults()
    rows = conn.execute(sa.text("SELECT id, system_prompt, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if _BACKUP_KEY not in meta:
            backup = {"system_prompt": row["system_prompt"]}
            for k in _META_PROMPT_KEYS:
                backup[k] = meta.get(k)
            meta[_BACKUP_KEY] = backup
        for k, v in defaults.items():
            meta[k] = v
        conn.execute(sa.text(
            "UPDATE bot_settings SET system_prompt = :sp, meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"sp": persona, "meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backup = meta.pop(_BACKUP_KEY, None)
        if not backup:
            continue
        sp = backup.get("system_prompt")
        for k in _META_PROMPT_KEYS:
            if backup.get(k) is None:
                meta.pop(k, None)
            else:
                meta[k] = backup[k]
        conn.execute(sa.text(
            "UPDATE bot_settings SET system_prompt = :sp, meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"sp": sp, "meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})
