"""normalize existing route labels for the prompt-driven pipeline

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-06-18 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import json


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKUP_KEY = "_backup_2026_06_18_route_labels"

ROUTES = {
    "catalog": {
        "label": "Каталог і прайси",
        "tool_name": "search_catalog",
        "patterns": ["ціна", "вартість", "прайс", "товар", "послуга", "робота", "наявність"],
    },
    "qa": {
        "label": "Записи знань і документи",
        "tool_name": "search_knowledge",
        "patterns": ["що робите", "що не робите", "умови", "правила", "гарантія", "як відбувається"],
    },
    "business_info": {
        "label": "Бізнес-факти",
        "tool_name": "get_business_info",
        "patterns": ["адреса", "графік", "години", "телефон", "оплата", "доставка", "контакти"],
    },
    "external_price": {
        "label": "Зовнішні ціни / постачальники",
        "tool_name": "search_parts",
        "patterns": ["ринкова ціна", "у постачальників", "зовнішня ціна", "ціна деталі"],
    },
    "web_search": {
        "label": "Зовнішній веб-пошук",
        "tool_name": "web_research",
        "patterns": ["що це", "ідентифікувати", "характеристика", "специфікація"],
    },
    "handoff": {
        "label": "Передача оператору",
        "tool_name": "escalate",
        "patterns": ["оператор", "людина", "менеджер", "подзвонити"],
    },
}


def upgrade() -> None:
    conn = op.get_bind()
    route_query = sa.text(
        "SELECT id, code, label, handler, intent_patterns, meta "
        "FROM knowledge_types WHERE code IN :codes"
    ).bindparams(sa.bindparam("codes", expanding=True))
    rows = conn.execute(route_query, {"codes": list(ROUTES.keys())}).mappings().all()

    for row in rows:
        spec = ROUTES.get(row["code"])
        if not spec:
            continue
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = {
                "label": row["label"],
                "handler": row["handler"],
                "intent_patterns": row["intent_patterns"] or [],
                "tool_name": meta.get("tool_name"),
            }
        meta["tool_name"] = meta.get("tool_name") or spec["tool_name"]

        patterns = list(row["intent_patterns"] or [])
        for pattern in spec["patterns"]:
            if pattern not in patterns:
                patterns.append(pattern)

        conn.execute(sa.text(
            "UPDATE knowledge_types "
            "SET label = :label, handler = :handler, intent_patterns = CAST(:patterns AS jsonb), meta = CAST(:meta AS jsonb) "
            "WHERE id = :id"
        ), {
            "id": row["id"],
            "label": spec["label"],
            "handler": meta["tool_name"],
            "patterns": json.dumps(patterns, ensure_ascii=False),
            "meta": json.dumps(meta, ensure_ascii=False),
        })


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, meta FROM knowledge_types WHERE meta ? :backup_key"
    ), {"backup_key": BACKUP_KEY}).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        backup = meta.pop(BACKUP_KEY, None) or {}
        conn.execute(sa.text(
            "UPDATE knowledge_types "
            "SET label = :label, handler = :handler, intent_patterns = CAST(:patterns AS jsonb), meta = CAST(:meta AS jsonb) "
            "WHERE id = :id"
        ), {
            "id": row["id"],
            "label": backup.get("label") or "",
            "handler": backup.get("handler") or "route",
            "patterns": json.dumps(backup.get("intent_patterns") or [], ensure_ascii=False),
            "meta": json.dumps(meta, ensure_ascii=False),
        })
