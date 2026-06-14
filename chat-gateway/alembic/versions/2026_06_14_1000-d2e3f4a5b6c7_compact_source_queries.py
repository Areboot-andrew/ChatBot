"""enforce compact source query prompts for texno routes

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-14 19:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import DEFAULT_DECISION_RULES, ROUTE_PROMPTS

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TOOL_TO_PROMPT = {
    "search_catalog": "catalog",
    "search_knowledge": "qa",
    "web_research": "web_search",
    "search_parts": "external_price",
    "get_business_info": "business_info",
    "escalate": "handoff",
}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, tenant_id, meta, system_prompt FROM bot_settings"
    )).mappings().all()

    for row in rows:
        persona = str(row["system_prompt"] or "")
        if "Інженер Андрон" not in persona and "texno.plus" not in persona:
            continue

        settings_meta = dict(row["meta"] or {})
        settings_meta["agent_decision_rules"] = DEFAULT_DECISION_RULES
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {
            "id": row["id"],
            "meta": json.dumps(settings_meta, ensure_ascii=False),
        })

        routes = conn.execute(sa.text(
            "SELECT id, code, meta FROM knowledge_types WHERE tenant_id = :tenant_id"
        ), {"tenant_id": row["tenant_id"]}).mappings().all()
        for route in routes:
            route_meta = dict(route["meta"] or {})
            prompt_key = TOOL_TO_PROMPT.get(str(route_meta.get("tool_name") or ""))
            if not prompt_key:
                continue
            route_meta.update(ROUTE_PROMPTS[prompt_key])
            conn.execute(sa.text(
                "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
            ), {
                "id": route["id"],
                "meta": json.dumps(route_meta, ensure_ascii=False),
            })


def downgrade() -> None:
    pass

