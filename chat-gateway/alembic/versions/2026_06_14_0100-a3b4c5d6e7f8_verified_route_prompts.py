"""verified route prompts and universal agent policy

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-14 01:00:00.000000
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import (
    DEFAULT_ANSWER_STYLE,
    DEFAULT_DECISION_RULES,
    DEFAULT_EVALUATION_RULES,
    DEFAULT_PARTS_INSTRUCTION,
    ROUTE_PROMPTS,
)

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _merged_route_meta(current, prompt_key: str, overwrite: bool) -> dict:
    meta = dict(current or {})
    defaults = ROUTE_PROMPTS[prompt_key]
    for key, value in defaults.items():
        if overwrite or not str(meta.get(key) or "").strip():
            meta[key] = value
    return meta


def upgrade() -> None:
    conn = op.get_bind()

    op.drop_constraint("knowledge_types_code_key", "knowledge_types", type_="unique")
    op.create_unique_constraint(
        "uq_knowledge_type_tenant_code", "knowledge_types", ["tenant_id", "code"]
    )

    settings_rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in settings_rows:
        meta = dict(row["meta"] or {})
        meta.update({
            "engine": "agent",
            "agent_decision_rules": DEFAULT_DECISION_RULES,
            "answer_style": DEFAULT_ANSWER_STYLE,
            "parts_instruction": DEFAULT_PARTS_INSTRUCTION,
            "tpl_evaluation_rules": DEFAULT_EVALUATION_RULES,
            "router_json_mode": True,
        })
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )

    # Keep the full tenant persona and its Ukrainian tone, but apply the corrected
    # source/price discipline to auto-seeded Andron tenants.
    try:
        with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as prompt_file:
            persona = prompt_file.read()
        conn.execute(
            sa.text("UPDATE bot_settings SET system_prompt = :persona WHERE system_prompt LIKE '%Інженер Андрон%'"),
            {"persona": persona},
        )
    except OSError:
        pass

    tenants = conn.execute(sa.text("SELECT id FROM tenants")).scalars().all()
    known_code_map = {
        "qa": "qa",
        "web_search": "web_search",
        "handoff": "handoff",
        "repair_check": "catalog",
    }

    for tenant_id in tenants:
        route_rows = conn.execute(
            sa.text("SELECT id, code, handler, meta FROM knowledge_types WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).mappings().all()

        present_tools = set()
        for route in route_rows:
            code = str(route["code"] or "")
            current_meta = dict(route["meta"] or {})
            prompt_key = known_code_map.get(code)
            if prompt_key:
                updated_meta = _merged_route_meta(current_meta, prompt_key, overwrite=True)
            else:
                tool_name = str(current_meta.get("tool_name") or "")
                tool_to_prompt = {
                    "search_catalog": "catalog",
                    "search_knowledge": "qa",
                    "search_parts": "external_price",
                    "web_research": "web_search",
                    "get_business_info": "business_info",
                    "escalate": "handoff",
                }
                prompt_key = tool_to_prompt.get(tool_name)
                if not prompt_key:
                    handler_to_prompt = {
                        "web_search_handler": "web_search",
                        "escalate": "handoff",
                    }
                    prompt_key = handler_to_prompt.get(str(route["handler"] or ""))
                updated_meta = (_merged_route_meta(current_meta, prompt_key, overwrite=False)
                                if prompt_key else current_meta)

            tool_name = str(updated_meta.get("tool_name") or "")
            if tool_name:
                present_tools.add(tool_name)
            conn.execute(
                sa.text("UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
                {"id": route["id"], "meta": json.dumps(updated_meta, ensure_ascii=False)},
            )

        missing_routes = [
            ("catalog", "Наш каталог: товари, послуги та ціни", "qa_handler",
             ["чи є", "чи робите", "ремонтуєте", "ціна", "скільки коштує"]),
            ("qa", "Затверджені Q&A та документи", "qa_handler",
             ["гарантія", "умови", "правила", "як відбувається"]),
            ("web_search", "Зовнішні характеристики та ідентифікація", "web_search_handler",
             ["характеристики", "сумісність", "що це", "точна модель"]),
            ("external_price", "Зовнішні ціни та постачальники", "web_search_handler",
             ["ціна деталі", "ринкова ціна", "у постачальників", "наявність деталі"]),
            ("business_info", "Графік, адреса, оплата та доставка", "qa_handler",
             ["коли працюєте", "адреса", "телефон", "оплата", "доставка", "коли прийти"]),
            ("handoff", "Передача оператору", "escalate",
             ["людина", "менеджер", "оператор", "подзвонити", "скарга"]),
        ]
        for prompt_key, label, handler, patterns in missing_routes:
            tool_name = ROUTE_PROMPTS[prompt_key]["tool_name"]
            if tool_name in present_tools:
                continue
            route_code = prompt_key
            conn.execute(
                sa.text(
                    "INSERT INTO knowledge_types "
                    "(id, tenant_id, code, label, handler, intent_patterns, enabled, meta) "
                    "VALUES (:id, :tenant_id, :code, :label, :handler, CAST(:patterns AS jsonb), true, CAST(:meta AS jsonb))"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "code": route_code,
                    "label": label,
                    "handler": handler,
                    "patterns": json.dumps(patterns, ensure_ascii=False),
                    "meta": json.dumps(ROUTE_PROMPTS[prompt_key], ensure_ascii=False),
                },
            )
            present_tools.add(tool_name)


def downgrade() -> None:
    pass
