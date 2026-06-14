"""restrict texno web research and separate part sales

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-06-14 17:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import DEFAULT_INTAKE_POLICY, ROUTE_PROMPTS

revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SERVICE_ONLY_PARTS_POLICY = (
    "texno.plus is a repair service and does not sell spare parts separately. "
    "For a separate part purchase, answer that parts are not sold separately and do not search suppliers, "
    "prices or stock. If the client requests installation/replacement as a repair, use only the internal "
    "service catalog and continue normal repair intake."
)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, tenant_id, meta, system_prompt FROM bot_settings"
    )).mappings().all()

    for row in rows:
        persona = str(row["system_prompt"] or "")
        if "Інженер Андрон" not in persona and "texno.plus" not in persona:
            continue

        meta = dict(row["meta"] or {})
        meta["intake_policy"] = DEFAULT_INTAKE_POLICY
        meta["web_research_mode"] = "identify_unknown_type_only"
        meta["parts_sales_mode"] = "service_only"
        meta["parts_instruction"] = SERVICE_ONLY_PARTS_POLICY
        conn.execute(sa.text(
            "UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)})

        try:
            with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as prompt_file:
                conn.execute(sa.text(
                    "UPDATE bot_settings SET system_prompt = :persona WHERE id = :id"
                ), {"id": row["id"], "persona": prompt_file.read()})
        except OSError:
            pass

        route_rows = conn.execute(sa.text(
            "SELECT id, code, meta FROM knowledge_types WHERE tenant_id = :tenant_id"
        ), {"tenant_id": row["tenant_id"]}).mappings().all()
        for route in route_rows:
            route_meta = dict(route["meta"] or {})
            tool_name = str(route_meta.get("tool_name") or "")
            if tool_name == "web_research":
                route_meta.update(ROUTE_PROMPTS["web_search"])
                conn.execute(sa.text(
                    "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb), "
                    "intent_patterns = CAST(:patterns AS jsonb) WHERE id = :id"
                ), {
                    "id": route["id"],
                    "meta": json.dumps(route_meta, ensure_ascii=False),
                    "patterns": json.dumps([
                        "що це", "який це пристрій", "який це прилад",
                        "тип пристрою", "тип приладу", "що це за штука",
                    ], ensure_ascii=False),
                })
            elif tool_name == "search_parts" or str(route["code"] or "") == "external_price":
                conn.execute(sa.text(
                    "UPDATE knowledge_types SET enabled = false WHERE id = :id"
                ), {"id": route["id"]})


def downgrade() -> None:
    pass

