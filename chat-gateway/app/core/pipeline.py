import time
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.tenant import BotSetting
from app.models.knowledge import QaPair
from app.models.services import ServicePrice
from app.core.intents import detect_intent
from app.core.tools import search_internet
from app.core.rag import search_knowledge
from app.core.prompt_builder import build_system_prompt
from app.core.llm import chat
import logging

logger = logging.getLogger(__name__)

async def process_message_pipeline(
    text: str, 
    history: list, 
    tenant_id: uuid.UUID, 
    db: AsyncSession
) -> str:
    """
    Core pipeline to process an incoming message, determine intent, 
    fetch necessary data, and return the LLM response.
    """
    # 1. Intent Recognition
    intent_data = await detect_intent(text, history)
    intent = intent_data.get("intent", "GENERAL")
    search_query = intent_data.get("query", "")
    
    if intent == "ERROR":
        return "Вибачте, сталася системна помилка (сервіс генерації недоступний)."
        
    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res.scalars().first()
    
    qa_facts = []
    rag_docs = []
    prices = []
    sys_prompt_addition = ""
    
    # 2. Fetch Data
    if intent == "CHECK_REPAIR_STATUS" or "статус" in text.lower():
        sys_prompt_addition = "\nДані з внутрішньої CRM системи:\n- Замовлення: телефон Samsung\n- Статус: В процесі діагностики\nОчікувана дата: Завтра."
        
    elif intent == "WEB_SEARCH" and search_query:
        search_result = search_internet(search_query, max_results=3)
        sys_prompt_addition = f"\nДані з інтернету (DuckDuckGo пошук за запитом '{search_query}'):\n{search_result}"
        
    else:
        # Fetching prices
        res_price = await db.execute(select(ServicePrice).where(ServicePrice.tenant_id == tenant_id).limit(100))
        prices = res_price.scalars().all()
        
        if prices:
            sys_prompt_addition = "\nДодаткова інформація з бази прайсів (ПРАЙС-ЛИСТ):\n" + "\n".join([f"- {p.name}: {p.price}" for p in prices])
            
        # SQL QA
        res_qa = await db.execute(select(QaPair).where(QaPair.tenant_id == tenant_id).limit(50))
        qa_facts = res_qa.scalars().all()
        
        # Qdrant RAG
        try:
            rag_docs = await search_knowledge(text, str(tenant_id), top_k=2)
        except Exception as e:
            logger.error(f"RAG search error: {e}")
            rag_docs = []
            
    # 3. Build Prompt
    sys_prompt = build_system_prompt(settings, qa_facts, rag_docs)
    sys_prompt += sys_prompt_addition
    
    temp = float(settings.temperature) if settings and settings.temperature else 0.7
    
    messages = [{"role": "system", "content": sys_prompt}]
    
    if history:
        for h in history:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    else:
        messages.append({"role": "user", "content": text})
        
    # 4. Generate LLM Response
    try:
        response_text = await chat(messages, temperature=temp)
        return response_text
    except Exception as e:
        logger.error(f"LLM Generation error: {e}")
        return "Вибачте, сталася помилка підключення до нейромережі."
