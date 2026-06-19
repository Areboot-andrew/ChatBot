"""Backfill minimal catalog item type.

Revision ID: 21f0a1b2c3d4
Revises: 20f0a1b2c3d4
Create Date: 2026-06-19 15:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "21f0a1b2c3d4"
down_revision = "20f0a1b2c3d4"
branch_labels = None
depends_on = None


service_categories = sa.table(
    "service_categories",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("title", sa.String),
    sa.column("description", sa.String),
    sa.column("meta", postgresql.JSONB),
)

service_prices = sa.table(
    "service_prices",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("category_id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("price", sa.String),
    sa.column("description", sa.String),
    sa.column("meta", postgresql.JSONB),
)


def _item_type(name: str) -> str:
    n = (name or "").lower()
    if "діагност" in n or "консультац" in n or "перевірка" in n:
        return "послуга"
    if "робота, без" in n or "без деталі" in n or "без акб" in n or "без диспле" in n:
        return "складна послуга"
    if any(w in n for w in ("заміна", "ремонт", "відновлення", "чистка", "встановлення", "налаштування", "прошивка")):
        return "послуга"
    return "послуга"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(
            service_prices.c.id,
            service_prices.c.name,
            service_prices.c.meta,
            service_categories.c.title.label("category_title"),
            service_categories.c.meta.label("category_meta"),
        )
        .select_from(
            service_prices.join(
                service_categories,
                service_prices.c.category_id == service_categories.c.id,
            )
        )
    ).mappings().all()

    for row in rows:
        current = dict(row["meta"] or {})
        # Respect manually edited item meta. Fill only the neutral item type.
        filled = {
            "item_type": current.get("item_type") or _item_type(row["name"]),
        }
        filled = {k: v for k, v in filled.items() if str(v or "").strip()}
        current.update(filled)
        conn.execute(
            service_prices.update()
            .where(service_prices.c.id == row["id"])
            .values(meta=current)
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.select(service_prices.c.id, service_prices.c.meta)).mappings().all()
    generated_keys = {"item_type"}
    for row in rows:
        meta = dict(row["meta"] or {})
        for key in generated_keys:
            meta.pop(key, None)
        conn.execute(
            service_prices.update()
            .where(service_prices.c.id == row["id"])
            .values(meta=meta)
        )
