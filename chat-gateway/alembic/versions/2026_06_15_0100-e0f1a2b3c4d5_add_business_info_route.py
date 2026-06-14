"""add a business_info route for tenants that lack one

Address / hours / phone questions could not route to get_business_info because
the tenant's Схема Логіки had no business_info route, so the weak router fell to
"answer" and the model invented contacts. This inserts a business_info route
(triggers -> get_business_info) for every tenant that does not already have a
route wired to get_business_info. Existing routes are untouched; idempotent.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-15 01:00:00.000000
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MARK = "_added_by_e0f1a2b3c4d5"
_TRIGGERS = [
    "адреса", "куди везти", "куди привезти", "де ви", "де знаходитесь",
    "графік", "години роботи", "коли працюєте", "коли відчинено",
    "телефон", "номер", "оплата", "доставка", "нова пошта",
]


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import ROUTE_PROMPTS
    canon = dict(ROUTE_PROMPTS.get("business_info", {}))

    tenants = conn.execute(sa.text("SELECT DISTINCT tenant_id FROM bot_settings")).mappings().all()
    for row in tenants:
        tid = row["tenant_id"]
        # already has a route that uses get_business_info?
        existing = conn.execute(sa.text(
            "SELECT id, meta, code FROM knowledge_types WHERE tenant_id = :tid"
        ), {"tid": tid}).mappings().all()
        has_biz = any(
            (dict(r["meta"] or {}).get("tool_name") == "get_business_info")
            or str(r["code"]) == "business_info"
            for r in existing
        )
        if has_biz:
            continue
        meta = dict(canon)
        meta["tool_name"] = "get_business_info"
        meta[_MARK] = True
        conn.execute(sa.text(
            "INSERT INTO knowledge_types (id, tenant_id, code, label, handler, intent_patterns, enabled, meta) "
            "VALUES (:id, :tid, :code, :label, :handler, CAST(:patterns AS jsonb), true, CAST(:meta AS jsonb))"
        ), {
            "id": str(uuid.uuid4()),
            "tid": tid,
            "code": "business_info",
            "label": "Графік, адреса, оплата та доставка",
            "handler": "qa_handler",
            "patterns": json.dumps(_TRIGGERS, ensure_ascii=False),
            "meta": json.dumps(meta, ensure_ascii=False),
        })


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, meta FROM knowledge_types WHERE code = 'business_info'"
    )).mappings().all()
    for r in rows:
        if dict(r["meta"] or {}).get(_MARK):
            conn.execute(sa.text("DELETE FROM knowledge_types WHERE id = :id"), {"id": r["id"]})
