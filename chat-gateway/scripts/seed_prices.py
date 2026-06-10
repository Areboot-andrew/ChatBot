import asyncio
import yaml
import sys
from sqlalchemy.future import select

# Add /app to sys path to import app modules if running outside docker? Wait, I will run this inside the docker container.
sys.path.append("/app")

from app.database import async_session_maker
from app.models.tenant import Tenant
from app.models.services import ServiceCategory, ServicePrice
from app.models.knowledge import QaPair

async def seed():
    async with async_session_maker() as db:
        res = await db.execute(select(Tenant))
        tenant = res.scalars().first()
        if not tenant:
            print("No tenant found. Exiting.")
            return

        with open("/app/knowledge_template.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 1. Import Categories and Prices
        for cat_data in data.get("categories", []):
            res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant.id, ServiceCategory.slug == cat_data["slug"]))
            cat = res_c.scalars().first()
            if not cat:
                cat = ServiceCategory(tenant_id=tenant.id, slug=cat_data["slug"])
                db.add(cat)
            
            cat.title = cat_data.get("title", "")
            cat.description = cat_data.get("description", "")
            cat.meta = {
                "detailed_description": cat_data.get("detailed_description", ""),
                "problems": cat_data.get("problems", [])
            }
            await db.commit()
            await db.refresh(cat)
            
            await db.execute(ServicePrice.__table__.delete().where(ServicePrice.category_id == cat.id))
            
            for price_data in cat_data.get("services", []):
                p = ServicePrice(
                    tenant_id=tenant.id,
                    category_id=cat.id,
                    name=price_data.get("name", ""),
                    price=str(price_data.get("price", ""))
                )
                db.add(p)
                
            for faq_data in cat_data.get("faqs", []):
                q = QaPair(
                    tenant_id=tenant.id,
                    question=faq_data.get("question", ""),
                    answer=faq_data.get("answer", ""),
                    category=f"FAQ_{cat.slug}"
                )
                db.add(q)
                
        # 2. Import Global FAQs
        for faq_data in data.get("global_faq", []):
            q = QaPair(
                tenant_id=tenant.id,
                question=faq_data.get("question", ""),
                answer=faq_data.get("answer", ""),
                category="FAQ_Global"
            )
            db.add(q)
            
        await db.commit()
        print("Import successful!")

if __name__ == "__main__":
    asyncio.run(seed())
