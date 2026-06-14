"""seed detailed repair symptom and intake library

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-06-14 14:30:00.000000
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

from app.core.intake_knowledge import REPAIR_INTAKE_CARDS

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tenants = conn.execute(sa.text(
        "SELECT DISTINCT tenant_id FROM bot_settings "
        "WHERE system_prompt ILIKE '%Інженер Андрон%' OR system_prompt ILIKE '%texno.plus%'"
    )).scalars().all()

    for tenant_id in tenants:
        for card in REPAIR_INTAKE_CARDS:
            params = {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "question": card["question"],
                "variants": json.dumps(card["variants"], ensure_ascii=False),
                "answer": card["answer"],
                "category": card["category"],
                "meta": json.dumps({
                    "kind": "repair_intake_card",
                    "source": "system_seed",
                    "version": 1,
                }, ensure_ascii=False),
            }
            updated = conn.execute(sa.text(
                "UPDATE qa_pairs SET question_variants = CAST(:variants AS jsonb), "
                "answer = :answer, category = :category, enabled = true, "
                "meta = CAST(:meta AS jsonb) "
                "WHERE tenant_id = :tenant_id AND question = :question"
            ), params)
            if updated.rowcount == 0:
                conn.execute(sa.text(
                    "INSERT INTO qa_pairs "
                    "(id, tenant_id, question, question_variants, answer, category, enabled, meta) "
                    "VALUES (:id, :tenant_id, :question, CAST(:variants AS jsonb), :answer, "
                    ":category, true, CAST(:meta AS jsonb))"
                ), params)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM qa_pairs WHERE meta->>'kind' = 'repair_intake_card' "
        "AND meta->>'source' = 'system_seed'"
    ))

