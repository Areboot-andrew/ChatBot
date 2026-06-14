"""enable verified external part prices for repair quotes

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-14 20:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import (
    DEFAULT_ANSWER_STYLE,
    DEFAULT_DECISION_RULES,
    DEFAULT_INTAKE_POLICY,
    DEFAULT_PARTS_INSTRUCTION,
    ROUTE_PROMPTS,
)

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, tenant_id, meta, system_prompt FROM bot_settings"
    )).mappings().all()

    try:
        with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as prompt_file:
            service_persona = prompt_file.read()
    except OSError:
        service_persona = ""

    for row in rows:
        persona = str(row["system_prompt"] or "")
        if "Інженер Андрон" not in persona and "texno.plus" not in persona:
            continue

        meta = dict(row["meta"] or {})
        meta.update({
            "agent_decision_rules": DEFAULT_DECISION_RULES,
            "answer_style": DEFAULT_ANSWER_STYLE,
            "intake_policy": DEFAULT_INTAKE_POLICY,
            "web_research_mode": "identify_unknown_type_only",
            "parts_sales_mode": "service_only",
            "external_part_price_mode": "repair_quote_only",
            "parts_instruction": DEFAULT_PARTS_INSTRUCTION,
        })
        conn.execute(sa.text(
            "UPDATE bot_settings SET system_prompt = CASE WHEN :persona <> '' THEN :persona ELSE system_prompt END, "
            "meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {
            "id": row["id"],
            "persona": service_persona,
            "meta": json.dumps(meta, ensure_ascii=False),
        })

        routes = conn.execute(sa.text(
            "SELECT id, code, meta FROM knowledge_types WHERE tenant_id = :tenant_id"
        ), {"tenant_id": row["tenant_id"]}).mappings().all()
        for route in routes:
            route_meta = dict(route["meta"] or {})
            tool_name = str(route_meta.get("tool_name") or "")
            code = str(route["code"] or "")
            if tool_name == "search_catalog":
                route_meta.update(ROUTE_PROMPTS["catalog"])
                conn.execute(sa.text(
                    "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
                ), {"id": route["id"], "meta": json.dumps(route_meta, ensure_ascii=False)})
            elif tool_name == "search_parts" or code == "external_price":
                route_meta.update(ROUTE_PROMPTS["external_price"])
                conn.execute(sa.text(
                    "UPDATE knowledge_types SET enabled = true, handler = 'web_search_handler', "
                    "intent_patterns = CAST(:patterns AS jsonb), meta = CAST(:meta AS jsonb) WHERE id = :id"
                ), {
                    "id": route["id"],
                    "patterns": json.dumps([
                        "ціна заміни дисплея",
                        "скільки коштує заміна акумулятора",
                        "вартість ремонту з деталлю",
                        "орієнтовна ціна ремонту",
                        "ціна роз'єму для заміни",
                    ], ensure_ascii=False),
                    "meta": json.dumps(route_meta, ensure_ascii=False),
                })
            elif tool_name == "web_research":
                route_meta.update(ROUTE_PROMPTS["web_search"])
                conn.execute(sa.text(
                    "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
                ), {"id": route["id"], "meta": json.dumps(route_meta, ensure_ascii=False)})


def downgrade() -> None:
    pass
