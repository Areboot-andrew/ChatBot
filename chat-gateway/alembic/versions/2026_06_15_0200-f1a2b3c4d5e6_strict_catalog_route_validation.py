"""strict catalog route validation

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-06-15 02:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VALIDATION_PROMPT = (
    "Compare complete phrases. The category and row must explicitly describe the same item/device type "
    "and requested product/service. Reject a row from another category even if it shares words such as "
    "screen, matrix, battery, board, bouquet or composition. Never assign an unfamiliar item to a broad "
    "category using general world knowledge: a returned list of categories is not proof that the requested "
    "item belongs to one of them. A category match can prove broad availability only when the requested "
    "generic device type is explicitly named by that category or a matching row; it cannot prove a specific "
    "price. A price is valid only from a matching internal row."
)
BACKUP_KEY = "_strict_catalog_validation_backup_f1a2b3c4d5e6"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        """
        SELECT id, meta
        FROM knowledge_types
        WHERE code IN ('catalog', 'repair_check')
           OR meta->>'tool_name' = 'search_catalog'
        """
    )).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if BACKUP_KEY not in meta:
            meta[BACKUP_KEY] = meta.get("result_validation_prompt")
        meta["result_validation_prompt"] = VALIDATION_PROMPT
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, meta FROM knowledge_types WHERE meta ? :backup_key"
    ), {"backup_key": BACKUP_KEY}).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        previous = meta.pop(BACKUP_KEY, None)
        if previous is None:
            meta.pop("result_validation_prompt", None)
        else:
            meta["result_validation_prompt"] = previous
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})
