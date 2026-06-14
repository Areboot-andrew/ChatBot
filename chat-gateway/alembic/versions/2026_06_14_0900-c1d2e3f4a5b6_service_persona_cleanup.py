"""replace remaining shop-oriented texno persona instructions

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-06-14 18:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import DEFAULT_ANSWER_STYLE, DEFAULT_INTAKE_POLICY

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
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
        meta["answer_style"] = DEFAULT_ANSWER_STYLE
        meta["intake_policy"] = DEFAULT_INTAKE_POLICY
        meta["web_research_mode"] = "identify_unknown_type_only"
        meta["parts_sales_mode"] = "service_only"
        conn.execute(sa.text(
            "UPDATE bot_settings SET system_prompt = CASE WHEN :persona <> '' THEN :persona ELSE system_prompt END, "
            "marketing_rules = '', meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {
            "id": row["id"],
            "persona": service_persona,
            "meta": json.dumps(meta, ensure_ascii=False),
        })

        conn.execute(sa.text(
            "UPDATE knowledge_types SET enabled = false "
            "WHERE tenant_id = :tenant_id AND "
            "(handler = 'site_search' OR meta->>'tool_name' = 'open_url' OR meta->>'tool_name' = 'search_parts')"
        ), {"tenant_id": row["tenant_id"]})


def downgrade() -> None:
    pass

