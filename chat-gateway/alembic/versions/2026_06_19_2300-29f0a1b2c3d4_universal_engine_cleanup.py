"""Clean active configuration for the universal prompt-driven engine.

Revision ID: 29f0a1b2c3d4
Revises: 28f0a1b2c3d4
Create Date: 2026-06-19 23:00:00.000000
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import (
    LEAN_ANSWER_PROMPT,
    LEAN_CONDUCT_PROMPT,
    LEAN_CONTROLLER_PROMPT,
    LEAN_WARNING_PROMPT,
    ROUTE_PROMPTS,
)


revision = "29f0a1b2c3d4"
down_revision = "28f0a1b2c3d4"
branch_labels = None
depends_on = None


SUPPORTED_ROUTE_TOOLS = {
    "search_catalog",
    "list_categories",
    "search_knowledge",
    "get_business_info",
    "web_research",
    "search_parts",
    "open_url",
    "escalate",
}

CANONICAL_ROUTES = {
    "catalog": {
        "label": "Каталог: категорії, товари/послуги, ціни",
        "patterns": ["чи є", "чи робите", "ремонтуєте", "скільки коштує", "ціна", "прайс"],
        "priority": "10",
        "prompt_key": "catalog",
    },
    "qa": {
        "label": "Знання: правила, процеси, Q&A",
        "patterns": ["гарантія", "умови", "як відбувається", "терміни", "правила"],
        "priority": "20",
        "prompt_key": "qa",
    },
    "business_info": {
        "label": "Бізнес-інфо: контакти, адреса, графік",
        "patterns": ["коли працюєте", "адреса", "телефон", "оплата", "доставка", "коли прийти"],
        "priority": "30",
        "prompt_key": "business_info",
    },
    "web_search": {
        "label": "Веб: визначити незрозумілий тип або характеристику",
        "patterns": ["характеристики", "сумісність", "що це", "яка модель", "специфікація"],
        "priority": "40",
        "prompt_key": "web_search",
    },
    "external_price": {
        "label": "Веб-ціни: запчастини/ринкові пропозиції",
        "patterns": ["ціна деталі", "ціна комплектуючої", "у постачальників", "ринкова ціна", "наявність деталі"],
        "priority": "50",
        "prompt_key": "external_price",
    },
    "handoff": {
        "label": "Передача оператору",
        "patterns": ["людина", "менеджер", "оператор", "скарга", "подзвонити"],
        "priority": "60",
        "prompt_key": "handoff",
    },
}

LEGACY_ROUTE_CODES = {
    "repair_check",
    "MODEL_PRYLADU",
    "model_pryladu",
    "classic",
    "agent",
    "old_router",
    "legacy_router",
    "legacy_agent",
}

BOT_META_KEYS_TO_DROP = (
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

ROUTE_META_KEYS_TO_DROP = (
    "reasoning",
    "next_step_prompt",
    "no_result_prompt",
    "fallback_action",
    "target_category",
    "prompt_key",
    "needed_fact",
    "action",
    "query",
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _clean_bot_meta(meta: dict) -> dict:
    cleaned = dict(meta or {})
    for key in BOT_META_KEYS_TO_DROP:
        cleaned.pop(key, None)
    cleaned.update(
        {
            "agent_max_iterations": str(cleaned.get("agent_max_iterations") or "3"),
            "controller_structural_fallback": "1",
            "conduct_enabled": "1",
            "conduct_warnings": str(cleaned.get("conduct_warnings") or "2"),
            "ban_message": str(cleaned.get("ban_message") or "Вітаю, вас забанено."),
            "lean_controller_prompt": LEAN_CONTROLLER_PROMPT,
            "lean_answer_prompt": LEAN_ANSWER_PROMPT,
            "lean_conduct_prompt": LEAN_CONDUCT_PROMPT,
            "lean_warning_prompt": LEAN_WARNING_PROMPT,
        }
    )
    return cleaned


def _route_meta(prompt_key: str, existing: dict | None = None) -> dict:
    target_url = ""
    if isinstance(existing, dict):
        target_url = str(existing.get("target_url") or "")
    meta = dict(ROUTE_PROMPTS[prompt_key])
    if target_url:
        meta["target_url"] = target_url
    return meta


def upgrade() -> None:
    conn = op.get_bind()

    tenants = conn.execute(sa.text("SELECT id FROM tenants")).mappings().all()
    settings = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in settings:
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": _json(_clean_bot_meta(row["meta"] or {}))},
        )

    for tenant in tenants:
        tenant_id = tenant["id"]
        existing_rows = conn.execute(
            sa.text("SELECT id, code, label, handler, meta FROM knowledge_types WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).mappings().all()
        by_code = {str(row["code"]): row for row in existing_rows}

        for code, cfg in CANONICAL_ROUTES.items():
            prompt_key = cfg["prompt_key"]
            meta = _route_meta(prompt_key, by_code.get(code, {}).get("meta") if by_code.get(code) else None)
            values = {
                "tenant_id": tenant_id,
                "code": code,
                "label": cfg["label"],
                "handler": meta["tool_name"],
                "patterns": _json(cfg["patterns"]),
                "priority": cfg["priority"],
                "meta": _json(meta),
            }
            if code in by_code:
                values["id"] = by_code[code]["id"]
                conn.execute(
                    sa.text(
                        """
                        UPDATE knowledge_types
                        SET label = :label,
                            handler = :handler,
                            intent_patterns = CAST(:patterns AS jsonb),
                            priority = :priority,
                            enabled = true,
                            meta = CAST(:meta AS jsonb)
                        WHERE id = :id
                        """
                    ),
                    values,
                )
            else:
                values["id"] = uuid.uuid4()
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO knowledge_types
                        (id, tenant_id, code, label, handler, intent_patterns, priority, enabled, meta)
                        VALUES
                        (:id, :tenant_id, :code, :label, :handler, CAST(:patterns AS jsonb), :priority, true, CAST(:meta AS jsonb))
                        """
                    ),
                    values,
                )

        refreshed = conn.execute(
            sa.text("SELECT id, code, handler, meta FROM knowledge_types WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).mappings().all()
        for row in refreshed:
            code = str(row["code"])
            meta = dict(row["meta"] or {})
            for key in ROUTE_META_KEYS_TO_DROP:
                meta.pop(key, None)
            tool = str(meta.get("tool_name") or row["handler"] or "").strip()
            if code in CANONICAL_ROUTES:
                continue
            if code in LEGACY_ROUTE_CODES or (tool and tool not in SUPPORTED_ROUTE_TOOLS):
                meta["disabled_reason"] = "legacy route disabled by universal prompt-driven engine cleanup"
                conn.execute(
                    sa.text(
                        """
                        UPDATE knowledge_types
                        SET enabled = false,
                            handler = 'route',
                            meta = CAST(:meta AS jsonb)
                        WHERE id = :id
                        """
                    ),
                    {"id": row["id"], "meta": _json(meta)},
                )
            else:
                conn.execute(
                    sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
                    {"id": row["id"], "meta": _json(meta)},
                )

    # Disable old seeded service-center knowledge so it no longer appears in
    # routing/content maps. User-created rows without these seed markers stay on.
    conn.execute(
        sa.text(
            """
            UPDATE service_categories
            SET enabled = false
            WHERE COALESCE(meta, '{}'::jsonb)->>'source' = 'local_seed_2026_06_18'
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE qa_pairs
            SET enabled = false
            WHERE COALESCE(meta, '{}'::jsonb)->>'source' IN ('local_seed_2026_06_18', 'system_seed')
               OR COALESCE(meta, '{}'::jsonb)->>'kind' = 'repair_intake_card'
            """
        )
    )


def downgrade() -> None:
    pass
