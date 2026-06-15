"""one consolidated force-sync of ALL lean prompts + canonical routes

Re-applies, in a single migration, everything we tuned across many small steps so
there is no doubt about what is in the DB:
- lean stage prompts: controller / answer / conduct / warning
- conduct + ban defaults (conduct_enabled, conduct_warnings, ban_message)
- the 6 canonical routes with correct handler/triggers/tool_name and the current
  route prose prompts (source_description / query_prompt / result_validation)
- disables any other (tangled) routes

Idempotent: run `alembic upgrade head` and everything matches the code defaults.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-15 15:00:00.000000
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# code, label, handler, triggers (capability/contact triggers included)
_INTENTS = [
    ("catalog", "Наш каталог: товари, послуги та ціни", "qa_handler",
     ["чи є", "чи робите", "ремонтуєте", "берете", "скільки коштує", "ціна", "прайс"]),
    ("qa", "Затверджені Q&A та документи", "qa_handler",
     ["гарантія", "умови", "як відбувається", "терміни", "правила",
      "чи ремонтуєте", "що ви ремонтуєте", "чи робите", "діагностика",
      "симптом", "причина", "чому", "як працює ремонт"]),
    ("web_search", "Зовнішні характеристики та ідентифікація", "web_search_handler",
     ["характеристики", "сумісність", "що це", "яка модель", "специфікація"]),
    ("external_price", "Зовнішні ціни та постачальники", "web_search_handler",
     ["ціна деталі", "ціна комплектуючої", "у постачальників", "ринкова ціна", "наявність деталі"]),
    ("business_info", "Графік, адреса, оплата та доставка", "qa_handler",
     ["коли працюєте", "адреса", "телефон", "оплата", "доставка", "коли прийти", "куди везти", "де ви"]),
    ("handoff", "Передача оператору", "escalate",
     ["людина", "менеджер", "оператор", "скарга", "подзвонити"]),
]


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import (
        ROUTE_PROMPTS, LEAN_CONTROLLER_PROMPT, LEAN_ANSWER_PROMPT,
        LEAN_CONDUCT_PROMPT, LEAN_WARNING_PROMPT,
    )

    # --- bot_settings: stage prompts + conduct/ban defaults ---
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        meta["lean_controller_prompt"] = LEAN_CONTROLLER_PROMPT
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        meta["lean_conduct_prompt"] = LEAN_CONDUCT_PROMPT
        meta["lean_warning_prompt"] = LEAN_WARNING_PROMPT
        meta.setdefault("conduct_enabled", "1")
        meta.setdefault("conduct_warnings", "2")
        if not str(meta.get("ban_message") or "").strip():
            meta["ban_message"] = "Вітаю, вас забанено."
        conn.execute(sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})

    # --- knowledge_types: canonical routes with current prompts ---
    canonical = [c for c, *_ in _INTENTS]
    tenants = conn.execute(sa.text("SELECT DISTINCT tenant_id FROM bot_settings")).mappings().all()
    for row in tenants:
        tid = row["tenant_id"]
        existing = {
            str(k["code"]): k for k in conn.execute(sa.text(
                "SELECT id, code FROM knowledge_types WHERE tenant_id = :t"
            ), {"t": tid}).mappings().all()
        }
        for code, label, handler, patterns in _INTENTS:
            canon = dict(ROUTE_PROMPTS.get(code, {}))
            meta = {
                "tool_name": canon.get("tool_name", ""),
                "source_description": canon.get("source_description", ""),
                "query_prompt": canon.get("query_prompt", ""),
                "result_validation_prompt": canon.get("result_validation_prompt", ""),
            }
            if code in existing:
                conn.execute(sa.text(
                    "UPDATE knowledge_types SET label=:l, handler=:h, enabled=true, "
                    "intent_patterns=CAST(:p AS jsonb), meta=CAST(:m AS jsonb) WHERE id=:id"
                ), {"l": label, "h": handler, "p": json.dumps(patterns, ensure_ascii=False),
                    "m": json.dumps(meta, ensure_ascii=False), "id": existing[code]["id"]})
            else:
                conn.execute(sa.text(
                    "INSERT INTO knowledge_types (id, tenant_id, code, label, handler, intent_patterns, enabled, meta) "
                    "VALUES (:id, :t, :c, :l, :h, CAST(:p AS jsonb), true, CAST(:m AS jsonb))"
                ), {"id": str(uuid.uuid4()), "t": tid, "c": code, "l": label, "h": handler,
                    "p": json.dumps(patterns, ensure_ascii=False),
                    "m": json.dumps(meta, ensure_ascii=False)})
        conn.execute(sa.text(
            "UPDATE knowledge_types SET enabled=false WHERE tenant_id=:t AND code <> ALL(:codes)"
        ), {"t": tid, "codes": canonical})


def downgrade() -> None:
    pass
