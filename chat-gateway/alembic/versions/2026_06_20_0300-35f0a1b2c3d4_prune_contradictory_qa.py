"""Prune contradictory / duplicate Q&A that misleads the model.

The Q&A base mixed two styles: structured intake guides ("do not name the
faulty part before inspection") and older site FAQ that DO name a part as the
cause ("the cause is the backlight / inverter / battery"). The model received
both and wavered. This removes the FAQ entries that contradict the intake guides
for the same category, plus a working-hours Q&A that conflicts with the
business_info card (different hours), and one duplicate coffee-machine entry.

Intake guides and direct "yes, we repair X" answers are kept — they are correct.

Deletion is by exact question text (safe, targeted). These rows can be
re-seeded from ServicePage.tsx via the texno knowledge seed if ever needed.

Revision ID: 35f0a1b2c3d4
Revises: 34f0a1b2c3d4
Create Date: 2026-06-20 03:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "35f0a1b2c3d4"
down_revision = "34f0a1b2c3d4"
branch_labels = None
depends_on = None


# Exact question texts to remove. Each either contradicts an intake guide for the
# same category (names a part as the cause) or conflicts/duplicates other data.
CONTRADICTORY_QUESTIONS = [
    # conflicts with business_info hours (11-17 vs 11-18)
    "графік роботи або години роботи",
    # name a part/cause as fact — contradicts the "симптом не діагноз" intake guides
    "Телевізор має звук, але немає зображення. Це ремонтується?",
    "Ноутбук гріється і шумить. Що потрібно робити?",
    "Станція не видає 220 В. Що може бути причиною?",
    "Що робити, якщо колонка не заряджається або швидко сідає?",
    "Один навушник грає тихіше. Це можна виправити?",
    # duplicate of the intake guide "Кавомашина не подає воду або каву"
    "Кавомашина не подає воду або погано готує каву. Що робити?",
]


def upgrade() -> None:
    conn = op.get_bind()
    res = conn.execute(
        sa.text("DELETE FROM qa_pairs WHERE question = ANY(:qs)"),
        {"qs": CONTRADICTORY_QUESTIONS},
    )
    print(f"[35f0] pruned contradictory/duplicate qa_pairs: {res.rowcount}")


def downgrade() -> None:
    # Data-only cleanup. Rows can be re-seeded from ServicePage.tsx if needed.
    pass
