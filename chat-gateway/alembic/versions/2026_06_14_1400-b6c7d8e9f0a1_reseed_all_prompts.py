"""reseed all universal prompts (persona, policies, route prompts)

Force-overwrites every UNIVERSAL instruction prompt for all tenants from the
canonical code sources, so prompt edits in code/persona file actually reach the
live tenants (seed only fills empty keys; these were frozen).

What it overwrites:
- bot_settings.system_prompt        <- givi_system_prompt.md
- bot_settings.meta policy keys     <- prompt_defaults.DEFAULT_*
- knowledge_types.meta route prompts<- prompt_defaults.ROUTE_PROMPTS (prose only)

What it deliberately does NOT touch (tenant-owned data/config):
  business_rules, marketing_rules, escalation_prompt, fallback_text,
  business_info, enabled_tools, modes, synonyms, ban_message, price_search_urls,
  parts_sites, fallback_sites, intent_patterns, tool_name, target_url.

Reversible: the previous values are snapshotted into a backup key before the
overwrite, and downgrade() restores them.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-06-14 22:00:00.000000
"""
from typing import Sequence, Union
from pathlib import Path
import json

from alembic import op
import sqlalchemy as sa

revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BACKUP_KEY = "_prompt_backup_b6c7d8e9f0a1"

# meta keys that carry a universal instruction prompt (not tenant data)
_META_PROMPT_KEYS = [
    "agent_decision_rules",
    "intake_policy",
    "conduct_policy",
    "answer_style",
    "parts_instruction",
    "tpl_evaluation_rules",
]
# route prose fields overwritten from ROUTE_PROMPTS (config like tool_name /
# intent_patterns / target_url is left untouched)
_ROUTE_PROMPT_KEYS = [
    "source_description",
    "reasoning",
    "query_prompt",
    "result_validation_prompt",
    "next_step_prompt",
    "no_result_prompt",
    "fallback_action",
]


def _load_persona() -> str:
    import app
    path = Path(app.__file__).resolve().parent / "givi_system_prompt.md"
    return path.read_text(encoding="utf-8")


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
    from app.core.prompt_defaults import ROUTE_PROMPTS

    # --- bot_settings: persona + meta policy prompts ---
    rows = conn.execute(sa.text(
        "SELECT id, system_prompt, meta FROM bot_settings"
    )).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        # snapshot current values once (do not clobber an existing backup)
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

    # --- knowledge_types: route prose prompts ---
    kt_rows = conn.execute(sa.text(
        "SELECT id, code, meta FROM knowledge_types"
    )).mappings().all()
    for kt in kt_rows:
        canon = ROUTE_PROMPTS.get(str(kt["code"]))
        if not canon:
            continue
        meta = dict(kt["meta"] or {})
        if _BACKUP_KEY not in meta:
            meta[_BACKUP_KEY] = {k: meta.get(k) for k in _ROUTE_PROMPT_KEYS}
        for k in _ROUTE_PROMPT_KEYS:
            if k in canon:
                meta[k] = canon[k]
        # set tool_name only if missing, never override a tenant choice
        if not (meta.get("tool_name") or "").strip() and canon.get("tool_name"):
            meta["tool_name"] = canon["tool_name"]
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": kt["id"]})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, meta FROM bot_settings"
    )).mappings().all()
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

    kt_rows = conn.execute(sa.text(
        "SELECT id, meta FROM knowledge_types"
    )).mappings().all()
    for kt in kt_rows:
        meta = dict(kt["meta"] or {})
        backup = meta.pop(_BACKUP_KEY, None)
        if not backup:
            continue
        for k in _ROUTE_PROMPT_KEYS:
            if backup.get(k) is None:
                meta.pop(k, None)
            else:
                meta[k] = backup[k]
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": kt["id"]})
