import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select

from app.models.tenant import Tenant, BotSetting
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def update_prompt():
    async with async_session_maker() as session:
        # 1. Get Tenant "ТехноплюсСервіс"
        res = await session.execute(select(Tenant).where(Tenant.name.ilike('%ТехноплюсСервіс%')))
        tenant = res.scalars().first()
        if not tenant:
            return
            
        # 2. Get BotSetting
        res = await session.execute(select(BotSetting).where(BotSetting.tenant_id == tenant.id))
        bot_setting = res.scalars().first()
        
        if bot_setting:
            with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as f:
                prompt_text = f.read()
            
            bot_setting.system_prompt = prompt_text
            await session.commit()
            print("System prompt updated!")

if __name__ == "__main__":
    asyncio.run(update_prompt())
