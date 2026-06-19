"""Purge legacy prompt meta and disable obsolete route seeds.

Revision ID: 23f0a1b2c3d4
Revises: 22f0a1b2c3d4
Create Date: 2026-06-19 17:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "23f0a1b2c3d4"
down_revision = "22f0a1b2c3d4"
branch_labels = None
depends_on = None


BOT_META_KEYS = (
    "price_triggers",
    "capability_triggers",
    "business_info_triggers",
    "brand_words",
    "part_words",
    "agent_decision_rules",
    "answer_style",
    "intake_policy",
    "conduct_policy",
    "parts_instruction",
    "tpl_evaluation_rules",
    "web_research_mode",
    "parts_sales_mode",
    "external_part_price_mode",
    "fallback_sites",
    "tpl_escalate_instruction",
    "enabled_tools",
    "router_json_mode",
    "lean_query_prompt",
    "lean_validator_prompt",
    "tpl_business_header",
    "tpl_marketing_header",
    "tpl_escalation_header",
    "tpl_qa_header",
    "tpl_rag_header",
    "tpl_footer",
    "tpl_router_rules",
)

ROUTE_META_KEYS = (
    "reasoning",
    "next_step_prompt",
    "no_result_prompt",
    "fallback_action",
    "target_category",
    "prompt_key",
)

def _jsonb_remove_expr(column: str, keys: tuple[str, ...]) -> str:
    expr = f"COALESCE({column}, '{{}}'::jsonb)"
    for key in keys:
        expr += f" - '{key}'"
    return expr


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            f"""
            UPDATE bot_settings
            SET meta = {_jsonb_remove_expr('meta', BOT_META_KEYS)}
            WHERE meta IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            f"""
            UPDATE knowledge_types
            SET meta = {_jsonb_remove_expr('meta', ROUTE_META_KEYS)}
            WHERE meta IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE knowledge_types
            SET enabled = false,
                meta = COALESCE(meta, '{}'::jsonb) || jsonb_build_object(
                    'disabled_reason',
                    'legacy pre-pipeline route; replace with catalog/qa/business_info/web_search/external_price/handoff route'
                )
            WHERE handler IN (
                'sql_price_search',
                'crm_status_check',
                'kompi_product_search',
                'qdrant_faq_search',
                'human_handoff'
            )
               OR code LIKE '%PRICE_CHECK%'
               OR code LIKE '%REPAIR_STATUS%'
               OR code LIKE '%BUY_PARTS%'
               OR code LIKE '%GENERAL_FAQ%'
               OR code LIKE '%ESCALATION%'
            """
        ),
    )


def downgrade() -> None:
    # This migration intentionally removes obsolete active configuration. Old
    # values are not restored because newer pipeline prompts own the behavior.
    pass
