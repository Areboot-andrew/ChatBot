"""restore the canonical lean route set (recovery after route renaming broke wiring)

Renaming routes in the panel left some routes without a correct tool_name, so the
controller no longer knew where each fact lives (address/FAQ fell back to the
catalog tool). This migration upserts the 6 canonical routes with the right
handler, triggers, tool_name and the universal route prompts, disables any other
(tangled) routes so they stop interfering, and seeds the lean stage prompts.

Idempotent and recoverable: non-canonical routes are only disabled, not deleted.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-15 07:00:00.000000
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INTENTS = [
    ("catalog", "Наш каталог: товари, послуги та ціни", "qa_handler",
     ["чи є", "чи робите", "ремонтуєте", "скільки коштує", "ціна", "прайс"]),
    ("qa", "Затверджені Q&A та документи", "qa_handler",
     ["гарантія", "умови", "як відбувається", "терміни", "правила"]),
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
    from app.core.prompt_defaults import ROUTE_PROMPTS

    tenants = conn.execute(sa.text("SELECT DISTINCT tenant_id FROM bot_settings")).mappings().all()
    canonical_codes = [c for c, *_ in _INTENTS]

    for row in tenants:
        tid = row["tenant_id"]
        existing = {
            str(r["code"]): r for r in conn.execute(sa.text(
                "SELECT id, code FROM knowledge_types WHERE tenant_id = :t"
            ), {"t": tid}).mappings().all()
        }
        # Upsert the canonical routes with correct wiring + universal prompts.
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
                    "intent_patterns=CAST(:p AS jsonb), meta=CAST(:m AS jsonb) "
                    "WHERE id=:id"
                ), {"l": label, "h": handler, "p": json.dumps(patterns, ensure_ascii=False),
                    "m": json.dumps(meta, ensure_ascii=False), "id": existing[code]["id"]})
            else:
                conn.execute(sa.text(
                    "INSERT INTO knowledge_types (id, tenant_id, code, label, handler, intent_patterns, enabled, meta) "
                    "VALUES (:id, :t, :c, :l, :h, CAST(:p AS jsonb), true, CAST(:m AS jsonb))"
                ), {"id": str(uuid.uuid4()), "t": tid, "c": code, "l": label, "h": handler,
                    "p": json.dumps(patterns, ensure_ascii=False),
                    "m": json.dumps(meta, ensure_ascii=False)})
        # Disable (not delete) any tangled non-canonical routes so they stop interfering.
        conn.execute(sa.text(
            "UPDATE knowledge_types SET enabled=false WHERE tenant_id=:t AND code <> ALL(:codes)"
        ), {"t": tid, "codes": canonical_codes})

    # Seed lean stage prompts + ban defaults where empty (reuse the seed helper logic).
    from app.core.prompt_defaults import (
        LEAN_CONTROLLER_PROMPT, LEAN_ANSWER_PROMPT, LEAN_CONDUCT_PROMPT, LEAN_WARNING_PROMPT,
    )
    stage = {
        "lean_controller_prompt": LEAN_CONTROLLER_PROMPT,
        "lean_answer_prompt": LEAN_ANSWER_PROMPT,
        "lean_conduct_prompt": LEAN_CONDUCT_PROMPT,
        "lean_warning_prompt": LEAN_WARNING_PROMPT,
        "ban_message": "Вітаю, вас забанено.",
        "conduct_enabled": "1",
        "conduct_warnings": "2",
    }
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for r in rows:
        meta = dict(r["meta"] or {})
        touched = False
        for k, v in stage.items():
            if not str(meta.get(k) or "").strip():
                meta[k] = v
                touched = True
        if touched:
            conn.execute(sa.text("UPDATE bot_settings SET meta=CAST(:m AS jsonb) WHERE id=:id"),
                         {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    # Non-destructive migration (canonical upsert + disable). Nothing to revert
    # safely without the pre-state snapshot; re-enable routes manually if needed.
    pass
