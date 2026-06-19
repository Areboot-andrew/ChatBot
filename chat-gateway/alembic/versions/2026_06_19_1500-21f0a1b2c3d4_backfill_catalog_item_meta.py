"""Backfill catalog item meta for hierarchical content map.

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


def _composition(name: str) -> str:
    n = (name or "").lower()
    if "діагност" in n:
        return "огляд пристрою, перевірка вузлів, пояснення варіантів ремонту; остаточна ціна після діагностики"
    if "робота, без" in n or "без деталі" in n or "без акб" in n or "без диспле" in n:
        return "робота майстра; запчастина/деталь рахується окремо після погодження"
    if "чистка" in n or "профілакти" in n or "обслуговування" in n:
        return "робота з очищення/обслуговування; витратні матеріали або деталі погоджуються окремо, якщо потрібні"
    if "ремонт" in n or "відновлення" in n:
        return "робота з діагностики причини та ремонту вузла; деталі й складні випадки погоджуються окремо"
    if "заміна" in n or "встановлення" in n:
        return "робота з заміни/встановлення; сумісна деталь або комплектуюча може рахуватись окремо"
    return "робота або умова з прайсу; точний склад дивитись у примітках і погоджувати з клієнтом"


def _availability(category_title: str) -> str:
    title = (category_title or "").lower()
    if "складання" in title or "апгрейд" in title:
        return "надаємо послугу в сервісі після погодження задачі"
    return "приймаємо в сервісі; точна можливість і ціна після огляду/діагностики"


def _characteristics(category: dict) -> str:
    meta = category.get("meta") if isinstance(category.get("meta"), dict) else {}
    parts = []
    brands = [str(x) for x in (meta.get("brands") or []) if str(x).strip()]
    problems = [str(x) for x in (meta.get("problems") or []) if str(x).strip()]
    if brands:
        parts.append("бренди/приклади: " + ", ".join(brands[:12]))
    if problems:
        parts.append("типові звернення: " + "; ".join(problems[:8]))
    desc = str(meta.get("detailedDescription") or "").strip()
    if desc:
        parts.append(desc[:700])
    return " | ".join(parts)


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
        # Respect manually edited item meta. Fill only missing fields.
        category = {"meta": row["category_meta"] or {}}
        brands = [str(x) for x in ((row["category_meta"] or {}).get("brands") or []) if str(x).strip()]
        filled = {
            "item_type": current.get("item_type") or _item_type(row["name"]),
            "brand": current.get("brand") or ", ".join(brands[:10]),
            "availability": current.get("availability") or _availability(row["category_title"]),
            "characteristics": current.get("characteristics") or _characteristics(category),
            "composition": current.get("composition") or _composition(row["name"]),
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
    generated_keys = {"item_type", "brand", "availability", "characteristics", "composition"}
    for row in rows:
        meta = dict(row["meta"] or {})
        for key in generated_keys:
            meta.pop(key, None)
        conn.execute(
            service_prices.update()
            .where(service_prices.c.id == row["id"])
            .values(meta=meta)
        )
