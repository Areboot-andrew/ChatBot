import asyncio
import json
import re
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select

from app.models.tenant import Tenant
from app.models.services import ServiceCategory, ServicePrice
from app.models.knowledge import QaPair
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

def extract_faqs_from_tsx(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Simple regex to extract the faqByCategory object block
    # This might be tricky, let's use a more robust way or just regex over the whole file
    faqs = {}
    
    # We find blocks like: speakers: [ { question: '...', answer: '...' }, ... ],
    # Actually, let's just regex all `{ question: '...', answer: '...' }` blocks
    # but we need categories.
    
    # Split by category names
    cats = ['speakers', 'phones', 'tvs', 'appliances', 'computers', 'headphones', 'power-stations', 'coffee-machines', 'pc-assembly']
    
    for cat in cats:
        # Find start of category array
        start_idx = content.find(f"    {cat}: [")
        if start_idx == -1:
            start_idx = content.find(f"    '{cat}': [")
            
        if start_idx != -1:
            end_idx = content.find("],", start_idx)
            cat_block = content[start_idx:end_idx]
            
            # Find all questions and answers
            q_pattern = r"question:\s*'([^']+)'"
            a_pattern = r"answer:\s*'([^']+)'"
            
            questions = re.findall(q_pattern, cat_block)
            answers = re.findall(a_pattern, cat_block)
            
            pairs = []
            for q, a in zip(questions, answers):
                pairs.append({'question': q, 'answer': a})
            faqs[cat] = pairs
            
    return faqs

async def seed():
    async with async_session_maker() as session:
        # 1. Get Tenant "ТехноплюсСервіс" (if it doesn't exist, get first tenant)
        res = await session.execute(select(Tenant).where(Tenant.name.ilike('%ТехноплюсСервіс%')))
        tenant = res.scalars().first()
        if not tenant:
            res = await session.execute(select(Tenant))
            tenant = res.scalars().first()
            if not tenant:
                print("No tenant found!")
                return
        
        tenant_id = tenant.id
        print(f"Using Tenant ID: {tenant_id}")
        
        # 2. Delete existing data for clean import
        # (Assuming cascade delete might not be needed if we do it manually, but let's just delete the records)
        await session.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant_id)) # fetch to delete or just use delete()
        from sqlalchemy import delete
        await session.execute(delete(ServicePrice).where(ServicePrice.tenant_id == tenant_id))
        await session.execute(delete(ServiceCategory).where(ServiceCategory.tenant_id == tenant_id))
        await session.execute(delete(QaPair).where(QaPair.tenant_id == tenant_id))
        await session.flush()
        
        # 3. Import Services from services.json
        with open("/app/app/services.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        from app.core.texno_price_catalog import TEXNO_SERVICE_PRICES
            
        categories = data.get("categories", [])
        for cat in categories:
            cat_obj = ServiceCategory(
                tenant_id=tenant_id,
                slug=cat.get("id"),
                title=cat.get("title"),
                description=cat.get("description"),
                meta={
                    "detailedDescription": cat.get("detailedDescription"),
                    "problems": cat.get("problems", [])
                }
            )
            session.add(cat_obj)
            await session.flush() # to get cat_obj.id
            
            services = cat.get("services", [])
            expanded = TEXNO_SERVICE_PRICES.get(cat.get("id"))
            if expanded:
                services = [{"name": name, "price": price} for name, price in expanded]
            for s in services:
                price_obj = ServicePrice(
                    tenant_id=tenant_id,
                    category_id=cat_obj.id,
                    name=s.get("name"),
                    price=s.get("price")
                )
                session.add(price_obj)
                
        # 4. Import FAQs
        faqs = extract_faqs_from_tsx("/app/app/ServicePage.tsx")
        qa_count = 0
        for cat_slug, pairs in faqs.items():
            for pair in pairs:
                qa_obj = QaPair(
                    tenant_id=tenant_id,
                    category=cat_slug,
                    question=pair['question'],
                    answer=pair['answer']
                )
                session.add(qa_obj)
                qa_count += 1
                
        await session.commit()
        print(f"Imported {len(categories)} categories and {qa_count} QA pairs.")

if __name__ == "__main__":
    asyncio.run(seed())
