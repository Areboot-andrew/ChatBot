import asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy import delete

from app.models.tenant import Tenant, KnowledgeType
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

intents_data = [
    {
        "code": "PRICE_CHECK",
        "label": "Запит Ціни / Прайсів",
        "handler": "sql_price_search",
        "intent_patterns": ["ціна", "скільки коштує", "вартість", "прайс", "розцінки"],
        "enabled": True
    },
    {
        "code": "REPAIR_STATUS",
        "label": "Перевірка статусу ремонту",
        "handler": "crm_status_check",
        "intent_patterns": ["статус", "чи готово", "відремонтували", "мій телефон", "мій ноут", "коли забирати"],
        "enabled": True
    },
    {
        "code": "BUY_PARTS",
        "label": "Підбір / Купівля запчастин",
        "handler": "kompi_product_search",
        "intent_patterns": ["купити", "замовити", "в наявності", "наявність", "продаєте"],
        "enabled": True
    },
    {
        "code": "GENERAL_FAQ",
        "label": "Загальні запитання (FAQ)",
        "handler": "qdrant_faq_search",
        "intent_patterns": ["графік", "де ви", "як доїхати", "гарантія", "відправляєте", "працюєте"],
        "enabled": True
    },
    {
        "code": "ESCALATION",
        "label": "Переведення на людину",
        "handler": "human_handoff",
        "intent_patterns": ["менеджер", "людина", "оператор", "зателефонувати", "поскаржитись", "керівник"],
        "enabled": True
    }
]

async def seed_intents():
    async with async_session_maker() as session:
        # Get Tenant
        res = await session.execute(select(Tenant).where(Tenant.name.ilike('%ТехноплюсСервіс%')))
        tenant = res.scalars().first()
        if not tenant:
            print("Tenant not found")
            return
            
        tenant_id = tenant.id
        
        # Cleanup old generic logic schemas if any
        await session.execute(delete(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id))
        
        # Insert new ones
        for item in intents_data:
            obj = KnowledgeType(
                tenant_id=tenant_id,
                code=item["code"] + str(uuid.uuid4())[:4], # Append random UUID in case code must be globally unique across tenants. Wait, code is globally unique? Let's assume yes.
                label=item["label"],
                handler=item["handler"],
                intent_patterns=item["intent_patterns"],
                enabled=item["enabled"]
            )
            # Make code globally unique properly or tenant scoped
            obj.code = f"{tenant_id}_{item['code']}"
            session.add(obj)
            
        await session.commit()
        print("Intents seeded successfully!")

if __name__ == "__main__":
    asyncio.run(seed_intents())
