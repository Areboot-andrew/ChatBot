import asyncio
from sqlalchemy.future import select
from app.database import async_session_maker
from app.models.tenant import BotSetting

async def update():
    async with async_session_maker() as db:
        res = await db.execute(select(BotSetting))
        settings = res.scalars().first()
        if settings:
            with open('../givi_system_prompt.md', 'r', encoding='utf-8') as f:
                settings.system_prompt = f.read()
            await db.commit()
            print('Prompt updated!')

if __name__ == "__main__":
    asyncio.run(update())
