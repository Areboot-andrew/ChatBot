import logging
import yaml
from sqlalchemy.future import select
from app.database import async_session_maker
from app.models.auth import User
from app.models.tenant import Tenant
from app.admin.auth import hash_password
from app.config import settings

logger = logging.getLogger(__name__)

async def seed_admin():
    async with async_session_maker() as db:
        # Seed Admin
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalars().first()
        
        if not admin:
            logger.info("Creating default admin user...")
            hashed_pw = hash_password(settings.ADMIN_DEFAULT_PASSWORD)
            new_admin = User(username="admin", hashed_password=hashed_pw)
            db.add(new_admin)
            await db.commit()
            logger.info("Default admin user created successfully.")

        # Seed First Tenant if empty
        res_t = await db.execute(select(Tenant))
        tenant = res_t.scalars().first()
        if not tenant:
            logger.info("Creating default tenant...")
            tenant = Tenant(name="Default Service", description="Main company")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        # Seed Prices from YAML if empty
        from app.models.services import ServiceCategory, ServicePrice
        from app.models.knowledge import QaPair
        res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant.id))
        if not res_c.scalars().first():
            logger.info("Database is empty. Seeding test data from knowledge_template.yaml...")
            try:
                with open("/app/knowledge_template.yaml", "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    
                for cat_data in data.get("categories", []):
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
                        
                for faq_data in data.get("global_faq", []):
                    q = QaPair(
                        tenant_id=tenant.id,
                        question=faq_data.get("question", ""),
                        answer=faq_data.get("answer", ""),
                        category="FAQ_Global"
                    )
                    db.add(q)
                    
                await db.commit()
                logger.info("Test data seeded successfully!")
            except Exception as e:
                logger.error(f"Failed to seed data: {e}")
