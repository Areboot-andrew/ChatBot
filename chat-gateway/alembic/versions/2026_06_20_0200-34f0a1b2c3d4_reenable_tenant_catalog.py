"""Re-enable tenant catalog + knowledge that 29f0 cleanup disabled.

The `universal_engine_cleanup` migration (29f0) set enabled=false on seeded
service-center categories and Q&A to make the engine tenant-neutral. But this
tenant relies on that catalog: `search_catalog` and `search_knowledge` filter
`enabled == True`, so they saw an EMPTY catalog and the bot answered
"не знайдено" / asked for the model, even though the prices exist in the DB.

Diagnosis confirmed by the live trace: the route SOURCE CONTENT MAP was the
empty placeholder (no enabled categories), and search_catalog returned the
"немає рядка або категорії" message.

This migration re-enables EXACTLY what 29f0 disabled (same source markers), so
the catalog and knowledge become visible to the pipeline again. Fully reversible.

Revision ID: 34f0a1b2c3d4
Revises: 33f0a1b2c3d4
Create Date: 2026-06-20 02:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "34f0a1b2c3d4"
down_revision = "33f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    cats = conn.execute(
        sa.text(
            """
            UPDATE service_categories
            SET enabled = true
            WHERE enabled = false
              AND COALESCE(meta->>'source', '') = 'local_seed_2026_06_18'
            """
        )
    )
    qa = conn.execute(
        sa.text(
            """
            UPDATE qa_pairs
            SET enabled = true
            WHERE enabled = false
              AND (
                COALESCE(meta->>'source', '') IN ('local_seed_2026_06_18', 'system_seed')
                OR COALESCE(meta->>'kind', '') = 'repair_intake_card'
              )
            """
        )
    )
    # Visible in the deploy logs so we know how many rows were re-enabled.
    print(f"[34f0] re-enabled categories={cats.rowcount}, qa_pairs={qa.rowcount}")


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE service_categories
            SET enabled = false
            WHERE COALESCE(meta->>'source', '') = 'local_seed_2026_06_18'
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE qa_pairs
            SET enabled = false
            WHERE COALESCE(meta->>'source', '') IN ('local_seed_2026_06_18', 'system_seed')
               OR COALESCE(meta->>'kind', '') = 'repair_intake_card'
            """
        )
    )
