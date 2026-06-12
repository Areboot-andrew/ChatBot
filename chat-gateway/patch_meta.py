import asyncio
from sqlalchemy import select
from app.database import async_session_maker
from app.models.tenant import BotSetting

NEW_RULES = """--- [АБСОЛЮТНЕ ПРАВИЛО: ЗАБОРОНА ГАЛЮЦИНАЦІЙ] ---
1. Якщо питання стосується технічних характеристик, сумісності, наявності чи цін — використовуй ВИКЛЮЧНО дані з блоків вище (Web Search, Прайс, FAQ).
2. Якщо в наданих текстах НЕМАЄ прямої відповіді — СУВОРО ЗАБОРОНЕНО вигадувати її з власної пам'яті.
3. Якщо даних немає, дай відповідь у своєму стилі, щось на кшталт: "Не маю точних технічних даних по цьому залізу, треба дивитись по факту" або "Цього зараз немає в базі, скинь точну модель".
4. НІЯКИХ припущень щодо сумісності. Або 100% підтвердження в контексті, або ти не знаєш."""

async def run():
    async with async_session_maker() as session:
        res = await session.execute(select(BotSetting))
        settings = res.scalars().all()
        for s in settings:
            meta = s.meta if s.meta else {}
            meta['tpl_evaluation_rules'] = NEW_RULES
            s.meta = meta
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(s, "meta")
        await session.commit()
        print(f"Updated {len(settings)} tenants with strict anti-hallucination rules.")

if __name__ == "__main__":
    asyncio.run(run())
