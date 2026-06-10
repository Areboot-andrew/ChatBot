import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select

from app.models.tenant import Tenant
from app.models.knowledge import QaPair
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

qa_data = [
    {
        "question": "Які у вас контакти та адреса? Де ви знаходитесь?",
        "answer": "Ми знаходимось за адресою: м. Львів, вул. Івана Огієнка, 15. Наш телефон: 067-385-15-60. Також можете писати нам у Telegram або Viber."
    },
    {
        "question": "Як відправити вам техніку Новою Поштою з іншого міста?",
        "answer": "Ви можете відправити пристрій Новою Поштою на м. Львів, відділення №48. Щоб отримати точні дані отримувача (ПІБ та телефон), будь ласка, напишіть нам у Telegram/Viber або зателефонуйте."
    },
    {
        "question": "Як відбувається процес ремонту? Який алгоритм?",
        "answer": "Процес наступний: 1. Ви приносите або відправляєте пристрій нам. 2. Ми проводимо безкоштовну діагностику (вона платна лише у разі вашої відмови від ремонту). 3. Ми зв'язуємось з вами, узгоджуємо ціну та терміни. 4. Виконуємо ремонт. 5. Тестуємо пристрій. 6. Ви забираєте його з сервісу або ми відправляємо назад поштою."
    }
]

async def seed_qa():
    async with async_session_maker() as session:
        res = await session.execute(select(Tenant).where(Tenant.name.ilike('%ТехноплюсСервіс%')))
        tenant = res.scalars().first()
        if not tenant:
            return
            
        tenant_id = tenant.id
        
        for item in qa_data:
            qa = QaPair(
                tenant_id=tenant_id,
                question=item["question"],
                answer=item["answer"],
                enabled=True
            )
            session.add(qa)
            
        await session.commit()
        print("Contacts and process added to QA Base!")

if __name__ == "__main__":
    asyncio.run(seed_qa())
