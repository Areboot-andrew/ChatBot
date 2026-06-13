"""texno tenant tone, conduct policy and broader routing vocabulary

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-14 02:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import (
    DEFAULT_ANSWER_STYLE,
    DEFAULT_CONDUCT_POLICY,
    DEFAULT_DECISION_RULES,
    DEFAULT_EVALUATION_RULES,
    DEFAULT_PARTS_INSTRUCTION,
    ROUTE_PROMPTS,
)
from app.core.agent import _CATALOG_SYNONYMS

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROUTE_PATTERNS = {
    "catalog": [
        "чи ремонтуєте", "берете в ремонт", "можна принести", "зробите", "ремонт",
        "заміна", "поміняти", "відновити", "діагностика", "ціна", "вартість",
        "скільки коштує", "прайс", "навушники", "гарнітура", "колонка", "акустика",
        "смартфон", "телефон", "ноутбук", "планшет", "монітор", "телевізор",
        "кавоварка", "кавомашина", "пилосос", "павербанк", "зарядна станція",
        "роз'єм", "гніздо", "порт", "дисплей", "екран", "батарея", "акумулятор",
        "не вмикається", "не заряджається", "гріється", "залив", "хрипить",
    ],
    "qa": [
        "гарантія", "умови", "правила", "як відбувається", "діагностика",
        "термін ремонту", "скільки часу", "нова пошта", "відправити", "самовивіз",
    ],
    "web_search": [
        "що це", "який це пристрій", "точна модель", "характеристики", "специфікація",
        "сумісність", "ревізія", "версія", "оригінал", "виробник", "bose", "босе",
        "marshall", "маршал", "major", "мейджор", "jbl", "джбл", "sony", "соні",
    ],
    "external_price": [
        "ціна деталі", "вартість запчастини", "скільки коштує деталь",
        "ринкова ціна", "у постачальників", "наявність деталі", "купити запчастину",
        "замовити дисплей", "замовити батарею", "модуль", "шлейф", "контролер",
    ],
    "business_info": [
        "адреса", "де ви", "як доїхати", "графік", "коли працюєте", "відчинено",
        "сьогодні працюєте", "завтра працюєте", "телефон", "номер", "оплата",
        "карта", "готівка", "доставка", "нова пошта", "коли прийти", "коли принести",
    ],
    "handoff": [
        "людина", "майстер", "інженер", "оператор", "менеджер", "керівник",
        "власник", "начальник", "подзвонити", "зателефонувати", "скарга",
    ],
}


def _prompt_key(code: str, handler: str, meta: dict) -> str:
    tool = str(meta.get("tool_name") or "")
    by_tool = {
        "search_catalog": "catalog",
        "search_knowledge": "qa",
        "web_research": "web_search",
        "search_parts": "external_price",
        "get_business_info": "business_info",
        "escalate": "handoff",
    }
    if tool in by_tool:
        return by_tool[tool]
    by_code = {
        "catalog": "catalog", "repair_check": "catalog", "qa": "qa",
        "web_search": "web_search", "external_price": "external_price",
        "business_info": "business_info", "handoff": "handoff",
    }
    if code in by_code:
        return by_code[code]
    if handler == "web_search_handler":
        return "web_search"
    if handler == "escalate":
        return "handoff"
    return ""


def upgrade() -> None:
    conn = op.get_bind()
    tenant_ids = conn.execute(
        sa.text(
            """
            SELECT DISTINCT t.id
            FROM tenants t
            LEFT JOIN bot_settings b ON b.tenant_id = t.id
            WHERE lower(coalesce(t.name, '')) LIKE '%техно%'
               OR lower(coalesce(t.name, '')) LIKE '%texno%'
               OR lower(coalesce(t.description, '')) LIKE '%texno.plus%'
               OR coalesce(b.system_prompt, '') LIKE '%Інженер Андрон%'
               OR coalesce(b.system_prompt, '') LIKE '%texno.plus%'
            """
        )
    ).scalars().all()

    synonym_text = "\n".join(
        f"{key}={','.join(values)}" for key, values in _CATALOG_SYNONYMS.items()
    )
    for tenant_id in tenant_ids:
        settings_rows = conn.execute(
            sa.text("SELECT id, meta FROM bot_settings WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).mappings().all()
        for row in settings_rows:
            meta = dict(row["meta"] or {})
            meta.update({
                "engine": "agent",
                "agent_decision_rules": DEFAULT_DECISION_RULES,
                "answer_style": DEFAULT_ANSWER_STYLE,
                "conduct_policy": DEFAULT_CONDUCT_POLICY,
                "ban_message": "Вітаю, вас забанено.",
                "parts_instruction": DEFAULT_PARTS_INSTRUCTION,
                "tpl_evaluation_rules": DEFAULT_EVALUATION_RULES,
                "catalog_synonyms": synonym_text,
                "router_json_mode": True,
            })
            conn.execute(
                sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
                {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
            )

        try:
            with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as prompt_file:
                persona = prompt_file.read()
            conn.execute(
                sa.text("UPDATE bot_settings SET system_prompt = :persona WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id, "persona": persona},
            )
        except OSError:
            pass

        routes = conn.execute(
            sa.text(
                "SELECT id, code, handler, intent_patterns, meta "
                "FROM knowledge_types WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        ).mappings().all()
        for route in routes:
            current_meta = dict(route["meta"] or {})
            key = _prompt_key(
                str(route["code"] or ""), str(route["handler"] or ""), current_meta
            )
            if not key:
                continue
            updated_meta = dict(current_meta)
            updated_meta.update(ROUTE_PROMPTS[key])
            patterns = list(dict.fromkeys(
                list(route["intent_patterns"] or []) + ROUTE_PATTERNS[key]
            ))
            conn.execute(
                sa.text(
                    "UPDATE knowledge_types "
                    "SET meta = CAST(:meta AS jsonb), intent_patterns = CAST(:patterns AS jsonb) "
                    "WHERE id = :id"
                ),
                {
                    "id": route["id"],
                    "meta": json.dumps(updated_meta, ensure_ascii=False),
                    "patterns": json.dumps(patterns, ensure_ascii=False),
                },
            )


def downgrade() -> None:
    pass
