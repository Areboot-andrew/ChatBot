"""Seed texno tenant knowledge from local curated sources.

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-06-18 14:00:00.000000
"""

from __future__ import annotations

import ast
import json
import os
import re
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


tenants = sa.table(
    "tenants",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.String),
)

service_categories = sa.table(
    "service_categories",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    sa.column("slug", sa.String),
    sa.column("title", sa.String),
    sa.column("description", sa.String),
    sa.column("meta", postgresql.JSONB),
    sa.column("enabled", sa.Boolean),
)

service_prices = sa.table(
    "service_prices",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    sa.column("category_id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("price", sa.String),
    sa.column("description", sa.String),
)

qa_pairs = sa.table(
    "qa_pairs",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
    sa.column("question", sa.String),
    sa.column("question_variants", postgresql.JSONB),
    sa.column("answer", sa.String),
    sa.column("category", sa.String),
    sa.column("enabled", sa.Boolean),
    sa.column("meta", postgresql.JSONB),
)


BASIC_QA = [
    {
        "category": "business_info",
        "question": "Які у вас контакти та адреса? Де ви знаходитесь?",
        "answer": (
            "Ми знаходимось за адресою: м. Львів, вул. Івана Огієнка, 15. "
            "Телефон: 067-385-15-60. Також можете писати нам у Telegram або Viber."
        ),
        "variants": [
            "де ви знаходитесь",
            "яка адреса",
            "контакти сервісу",
            "як з вами зв'язатись",
        ],
    },
    {
        "category": "business_info",
        "question": "Як відправити вам техніку Новою Поштою з іншого міста?",
        "answer": (
            "Можете відправити пристрій Новою Поштою у Львів, відділення №48. "
            "Щоб отримати точні дані отримувача, напишіть нам у Telegram/Viber або зателефонуйте."
        ),
        "variants": [
            "чи можна відправити новою поштою",
            "як надіслати техніку",
            "ремонт з іншого міста",
        ],
    },
    {
        "category": "business_info",
        "question": "Як відбувається процес ремонту?",
        "answer": (
            "Зазвичай так: ви приносите або відправляєте пристрій, ми проводимо діагностику, "
            "погоджуємо ціну й строки, виконуємо ремонт, тестуємо техніку і після цього ви її забираєте "
            "або ми відправляємо назад."
        ),
        "variants": [
            "який порядок ремонту",
            "як проходить ремонт",
            "що буде після діагностики",
        ],
    },
    {
        "category": "policy",
        "question": "Чи продаєте ви запчастини окремо?",
        "answer": "Окремо запчастини не продаємо, ми сервісний центр. Можемо підібрати деталь у межах ремонту.",
        "variants": [
            "купити запчастину",
            "продайте деталь",
            "чи можна купити дисплей",
            "чи продаєте акумулятори",
        ],
    },
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_services() -> list[dict]:
    path = _project_root() / "app" / "services.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("categories", [])


def _load_expanded_prices() -> dict[str, list[tuple[str, str]]]:
    path = _project_root() / "app" / "core" / "texno_price_catalog.py"
    if not path.exists():
        return {}
    source = path.read_text(encoding="utf-8")
    match = re.search(r"TEXNO_SERVICE_PRICES\s*=\s*(\{.*\})", source, re.S)
    if not match:
        return {}
    try:
        return ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return {}


def _load_service_page_faqs() -> list[dict]:
    path = _project_root() / "app" / "ServicePage.tsx"
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8")
    slugs = [
        "speakers",
        "phones",
        "tvs",
        "appliances",
        "computers",
        "headphones",
        "power-stations",
        "coffee-machines",
        "pc-assembly",
    ]
    rows: list[dict] = []
    for slug in slugs:
        start = content.find(f"    {slug}: [")
        if start == -1:
            start = content.find(f"    '{slug}': [")
        if start == -1:
            continue
        end = content.find("],", start)
        if end == -1:
            continue
        block = content[start:end]
        questions = re.findall(r"question:\s*'([^']+)'", block)
        answers = re.findall(r"answer:\s*'([^']+)'", block)
        for question, answer in zip(questions, answers):
            rows.append(
                {
                    "category": f"service_faq:{slug}",
                    "question": question,
                    "answer": answer,
                    "variants": [],
                }
            )
    return rows


def _tenant_id(conn):
    wanted = os.getenv("SEED_TENANT_NAME", "ТехноплюсСервіс")
    row = conn.execute(
        sa.select(tenants.c.id).where(tenants.c.name.ilike(f"%{wanted}%")).limit(1)
    ).first()
    if row:
        return row.id
    row = conn.execute(sa.select(tenants.c.id).limit(1)).first()
    return row.id if row else None


def _upsert_category(conn, tenant_id, category: dict):
    slug = category.get("id") or category.get("slug")
    if not slug:
        return None

    meta = {
        "source": "local_seed_2026_06_18",
        "metaTitle": category.get("metaTitle"),
        "metaDescription": category.get("metaDescription"),
        "keywords": category.get("keywords"),
        "detailedDescription": category.get("detailedDescription") or category.get("detailed_description"),
        "problems": category.get("problems", []),
        "brands": [brand.get("name") for brand in category.get("brands", []) if brand.get("name")],
        "seed_note": (
            "This row is the tenant service/product category. Description and meta are intended "
            "for route-level LLM filtering, not for direct blind quoting."
        ),
    }

    existing = conn.execute(
        sa.select(service_categories.c.id).where(
            service_categories.c.tenant_id == tenant_id,
            service_categories.c.slug == slug,
        )
    ).first()
    values = {
        "title": category.get("title") or slug,
        "description": category.get("description") or "",
        "meta": meta,
        "enabled": True,
    }
    if existing:
        conn.execute(
            service_categories.update()
            .where(service_categories.c.id == existing.id)
            .values(**values)
        )
        return existing.id

    category_id = uuid.uuid4()
    conn.execute(
        service_categories.insert().values(
            id=category_id,
            tenant_id=tenant_id,
            slug=slug,
            **values,
        )
    )
    return category_id


def _price_description(category: dict, service_name: str) -> str:
    problems = category.get("problems", [])
    hints = "; ".join(problems[:4])
    note = "Орієнтовна позиція для відповіді клієнту; точну суму погоджувати після діагностики."
    if "(робота" in service_name or "без " in service_name:
        note += " Якщо в назві вказано 'без деталі', запчастина рахується окремо."
    if hints:
        note += f" Типові симптоми категорії: {hints}."
    return note


def _upsert_price(conn, tenant_id, category_id, category: dict, item: dict):
    name = (item.get("name") or "").strip()
    price = str(item.get("price") or "").strip()
    if not name or not price:
        return
    existing = conn.execute(
        sa.select(service_prices.c.id).where(
            service_prices.c.tenant_id == tenant_id,
            service_prices.c.category_id == category_id,
            service_prices.c.name == name,
        )
    ).first()
    values = {
        "price": price,
        "description": item.get("description") or _price_description(category, name),
    }
    if existing:
        conn.execute(
            service_prices.update()
            .where(service_prices.c.id == existing.id)
            .values(**values)
        )
        return
    conn.execute(
        service_prices.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            category_id=category_id,
            name=name,
            **values,
        )
    )


def _upsert_qa(conn, tenant_id, item: dict):
    question = (item.get("question") or "").strip()
    answer = (item.get("answer") or "").strip()
    if not question or not answer:
        return
    existing = conn.execute(
        sa.select(qa_pairs.c.id).where(
            qa_pairs.c.tenant_id == tenant_id,
            qa_pairs.c.question == question,
        )
    ).first()
    values = {
        "question_variants": item.get("variants", []),
        "answer": answer,
        "category": item.get("category") or "general",
        "enabled": True,
        "meta": {
            "source": "local_seed_2026_06_18",
            "seed_note": "Curated tenant knowledge for route-level filtering and answer grounding.",
        },
    }
    if existing:
        conn.execute(qa_pairs.update().where(qa_pairs.c.id == existing.id).values(**values))
        return
    conn.execute(
        qa_pairs.insert().values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            question=question,
            **values,
        )
    )


def upgrade() -> None:
    conn = op.get_bind()
    tenant_id = _tenant_id(conn)
    if not tenant_id:
        return

    categories = _load_services()
    expanded_prices = _load_expanded_prices()

    for category in categories:
        category_id = _upsert_category(conn, tenant_id, category)
        if not category_id:
            continue
        slug = category.get("id") or category.get("slug")
        prices = expanded_prices.get(slug)
        if prices:
            price_items = [{"name": name, "price": price} for name, price in prices]
        else:
            price_items = category.get("services", [])
        for item in price_items:
            _upsert_price(conn, tenant_id, category_id, category, item)

    for item in BASIC_QA + _load_service_page_faqs():
        _upsert_qa(conn, tenant_id, item)


def downgrade() -> None:
    # Data-only seed. We intentionally do not delete tenant knowledge on downgrade.
    pass
