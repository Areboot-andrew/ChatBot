"""universal prompt boundaries

Refresh the editable Lean prompts and the three prompts owned by every known
route. Tenant persona, business data, route triggers and source configuration
remain untouched. Previous values are stored for a reversible downgrade.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-15 06:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SETTINGS_BACKUP = "_universal_prompt_boundaries_v6_backup"
ROUTE_BACKUP = "_universal_route_boundaries_v6_backup"
STAGE_FIELDS = (
    "lean_controller_prompt",
    "lean_answer_prompt",
    "lean_conduct_prompt",
    "lean_warning_prompt",
)
OBSOLETE_SETTINGS_FIELDS = (
    "agent_decision_rules",
    "answer_style",
    "intake_policy",
    "conduct_policy",
    "parts_instruction",
    "tpl_evaluation_rules",
    "web_research_mode",
    "parts_sales_mode",
    "external_part_price_mode",
    "tpl_escalate_instruction",
)
ROUTE_FIELDS = ("source_description", "query_prompt", "result_validation_prompt")
OBSOLETE_ROUTE_FIELDS = (
    "reasoning",
    "next_step_prompt",
    "no_result_prompt",
    "fallback_action",
)
TOOL_TO_CANON = {
    "search_catalog": "catalog",
    "search_knowledge": "qa",
    "web_research": "web_search",
    "search_parts": "external_price",
    "get_business_info": "business_info",
    "escalate": "handoff",
}


def _defaults():
    from app.core.prompt_defaults import (
        LEAN_ANSWER_PROMPT,
        LEAN_CONDUCT_PROMPT,
        LEAN_CONTROLLER_PROMPT,
        LEAN_WARNING_PROMPT,
        ROUTE_PROMPTS,
    )
    return {
        "lean_controller_prompt": LEAN_CONTROLLER_PROMPT,
        "lean_answer_prompt": LEAN_ANSWER_PROMPT,
        "lean_conduct_prompt": LEAN_CONDUCT_PROMPT,
        "lean_warning_prompt": LEAN_WARNING_PROMPT,
    }, ROUTE_PROMPTS


def upgrade() -> None:
    conn = op.get_bind()
    stage_defaults, route_defaults = _defaults()

    for row in conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all():
        meta = dict(row["meta"] or {})
        changed_fields = STAGE_FIELDS + OBSOLETE_SETTINGS_FIELDS
        if SETTINGS_BACKUP not in meta:
            meta[SETTINGS_BACKUP] = {field: meta.get(field) for field in changed_fields}
        meta.update(stage_defaults)
        for field in OBSOLETE_SETTINGS_FIELDS:
            meta.pop(field, None)
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})

    for row in conn.execute(sa.text(
        "SELECT id, code, meta FROM knowledge_types"
    )).mappings().all():
        meta = dict(row["meta"] or {})
        code = str(row["code"])
        canon_code = code if code in route_defaults else TOOL_TO_CANON.get(
            str(meta.get("tool_name") or "")
        )
        defaults = route_defaults.get(canon_code or "")
        if not defaults:
            continue
        changed_fields = ROUTE_FIELDS + OBSOLETE_ROUTE_FIELDS
        if ROUTE_BACKUP not in meta:
            meta[ROUTE_BACKUP] = {field: meta.get(field) for field in changed_fields}
        for field in ROUTE_FIELDS:
            meta[field] = defaults[field]
        for field in OBSOLETE_ROUTE_FIELDS:
            meta.pop(field, None)
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})


def downgrade() -> None:
    conn = op.get_bind()

    for row in conn.execute(sa.text(
        "SELECT id, meta FROM bot_settings WHERE meta ? :key"
    ), {"key": SETTINGS_BACKUP}).mappings().all():
        meta = dict(row["meta"] or {})
        backup = meta.pop(SETTINGS_BACKUP)
        for field in STAGE_FIELDS + OBSOLETE_SETTINGS_FIELDS:
            if backup.get(field) is None:
                meta.pop(field, None)
            else:
                meta[field] = backup[field]
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})

    for row in conn.execute(sa.text(
        "SELECT id, meta FROM knowledge_types WHERE meta ? :key"
    ), {"key": ROUTE_BACKUP}).mappings().all():
        meta = dict(row["meta"] or {})
        backup = meta.pop(ROUTE_BACKUP)
        for field in ROUTE_FIELDS + OBSOLETE_ROUTE_FIELDS:
            if backup.get(field) is None:
                meta.pop(field, None)
            else:
                meta[field] = backup[field]
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})
