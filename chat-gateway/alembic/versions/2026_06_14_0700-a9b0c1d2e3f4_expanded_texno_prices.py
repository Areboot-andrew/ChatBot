"""expand texno service prices with June 2026 market buffer

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-06-14 16:00:00.000000
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

from app.core.texno_price_catalog import LEGACY_TEXNO_PRICE_NAMES, TEXNO_SERVICE_PRICES

revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tenants = conn.execute(sa.text(
        "SELECT DISTINCT tenant_id FROM bot_settings "
        "WHERE system_prompt ILIKE '%Інженер Андрон%' OR system_prompt ILIKE '%texno.plus%'"
    )).scalars().all()

    for tenant_id in tenants:
        categories = conn.execute(sa.text(
            "SELECT id, slug FROM service_categories WHERE tenant_id = :tenant_id"
        ), {"tenant_id": tenant_id}).mappings().all()
        by_slug = {row["slug"]: row["id"] for row in categories}

        for slug, prices in TEXNO_SERVICE_PRICES.items():
            category_id = by_slug.get(slug)
            if not category_id:
                continue
            legacy_names = LEGACY_TEXNO_PRICE_NAMES.get(slug, [])
            if legacy_names:
                conn.execute(sa.text(
                    "DELETE FROM service_prices WHERE tenant_id = :tenant_id "
                    "AND category_id = :category_id AND name = ANY(:names)"
                ), {
                    "tenant_id": tenant_id,
                    "category_id": category_id,
                    "names": legacy_names,
                })
            for name, price in prices:
                params = {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "category_id": category_id,
                    "name": name,
                    "price": price,
                }
                updated = conn.execute(sa.text(
                    "UPDATE service_prices SET price = :price "
                    "WHERE tenant_id = :tenant_id AND category_id = :category_id AND name = :name"
                ), params)
                if updated.rowcount == 0:
                    conn.execute(sa.text(
                        "INSERT INTO service_prices (id, tenant_id, category_id, name, price) "
                        "VALUES (:id, :tenant_id, :category_id, :name, :price)"
                    ), params)


def downgrade() -> None:
    pass
