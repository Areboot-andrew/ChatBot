"""Improve fallback text when LLM is unavailable.

Revision ID: 32f0a1b2c3d4
Revises: 31f0a1b2c3d4
Create Date: 2026-06-19 23:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "32f0a1b2c3d4"
down_revision = "31f0a1b2c3d4"
branch_labels = None
depends_on = None


NEW_FALLBACK = "Зараз технічна заминка з відповіддю. Напишіть ще раз за хвилину."
OLD_FALLBACKS = (
    "",
    "Вибачте, зараз не можу відповісти — спробуйте ще раз трохи згодом.",
    "Вибачте, сталася технічна помилка.",
    "Технічна заминка, спробуйте ще раз.",
    "Service temporarily unavailable.",
)


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        """
        UPDATE bot_settings
        SET fallback_text = :new_fallback
        WHERE fallback_text IS NULL
           OR btrim(fallback_text) IN :old_fallbacks
        """
    ).bindparams(sa.bindparam("old_fallbacks", expanding=True))
    conn.execute(
        stmt,
        {"new_fallback": NEW_FALLBACK, "old_fallbacks": list(OLD_FALLBACKS)},
    )


def downgrade() -> None:
    pass
