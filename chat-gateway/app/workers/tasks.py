from arq.connections import RedisSettings
from arq.cron import cron
from app.config import settings
import logging

logger = logging.getLogger(__name__)

async def startup(ctx):
    logger.info("Worker started")

async def shutdown(ctx):
    logger.info("Worker shutting down")

async def llm_task(ctx, channel_id: str, chat_id: str, text: str):
    """
    Background task to process LLM request sequentially (concurrency 1)
    """
    logger.info(f"Processing LLM task for {channel_id}:{chat_id}")
    # TODO: Implement full pipeline (Intent -> RAG/SQL -> Prompt -> LLM -> Send)
    from app.core.llm import chat
    response = await chat([{"role": "user", "content": text}])
    logger.info(f"LLM Response: {response}")
    return response

async def sync_catalog_task(ctx):
    """
    Periodic task to download and parse catalog from the specified JSON Feed URLs
    for all tenants.
    """
    logger.info("Starting periodic catalog sync for all tenants...")
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.future import select
    from app.database import engine
    from app.models.tenant import Tenant
    from app.models.services import ServiceCategory, ServicePrice
    import httpx
    import re
    
    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session_maker() as session:
        res = await session.execute(select(Tenant))
        tenants = res.scalars().all()
        
        async with httpx.AsyncClient() as client:
            for tenant in tenants:
                if not tenant.enabled: continue
                sync_url = tenant.meta.get("catalog_sync_url") if tenant.meta else None
                if not sync_url: continue
                
                logger.info(f"Syncing catalog for tenant {tenant.name} from {sync_url}")
                try:
                    resp = await client.get(sync_url, timeout=30)
                    resp.raise_for_status()
                    data = resp.json()
                    categories = data if isinstance(data, list) else data.get("categories", [])
                    
                    count = 0
                    for cat_data in categories:
                        title = cat_data.get("title") or cat_data.get("name")
                        if not title: continue
                        
                        res_c = await session.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant.id, ServiceCategory.title == title))
                        cat = res_c.scalars().first()
                        if not cat:
                            slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
                            cat = ServiceCategory(tenant_id=tenant.id, title=title, slug=slug, description="")
                            session.add(cat)
                            await session.flush()
                            
                        services = cat_data.get("prices") or cat_data.get("services") or []
                        for s in services:
                            s_name = s.get("name")
                            s_price = str(s.get("price"))
                            if not s_name: continue
                            
                            res_p = await session.execute(select(ServicePrice).where(ServicePrice.category_id == cat.id, ServicePrice.name == s_name))
                            price_obj = res_p.scalars().first()
                            if price_obj:
                                price_obj.price = s_price
                            else:
                                price_obj = ServicePrice(tenant_id=tenant.id, category_id=cat.id, name=s_name, price=s_price)
                                session.add(price_obj)
                            count += 1
                            
                    await session.commit()
                    logger.info(f"Successfully synced {count} prices for {tenant.name}")
                except Exception as e:
                    logger.error(f"Error syncing for tenant {tenant.name}: {e}")

# Parse REDIS_URL to get host/port
url = settings.REDIS_URL.replace("redis://", "")
host = url.split(":")[0] if ":" in url else "localhost"
port = int(url.split(":")[1].split("/")[0]) if ":" in url else 6379

class WorkerSettings:
    functions = [llm_task]
    cron_jobs = [
        cron(sync_catalog_task, hour={0, 12}, minute=0) # Run at midnight and noon
    ]
    redis_settings = RedisSettings(host=host, port=port)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1 # Concurrency 1 for local LLM (for now)
