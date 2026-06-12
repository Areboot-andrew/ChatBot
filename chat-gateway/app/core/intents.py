import json
import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.llm import chat
from app.models.tenant import KnowledgeType, BotSetting

logger = logging.getLogger(__name__)

async def detect_intent(text: str, history: list, tenant_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Analyzes the user's message and routes it to the correct subsystem.
    """
    res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res_s.scalars().first()
    
    sys_prompt = """
    You are an intent router for a technical repair chatbot.
    Analyze the user's message IN THE CONTEXT of the conversation history.
    Return a JSON object with:
    - "intent": one of ["CHECK_REPAIR_STATUS", "WEB_SEARCH", "RAG_SEARCH", "GENERAL"]
    - "query": a precise English technical search query if search is needed, otherwise empty.
    
    Rules for intents:
    - "CHECK_REPAIR_STATUS": if the user explicitly asks about the status of their repair.
    - "WEB_SEARCH": if the user asks for technical specs, pinouts, or compatibility. CRITICAL: If the user asks about compatibility between parts (e.g. "Biostar A78MD + fx6300"), you MUST generate a query that explicitly asks for sockets and specs to avoid false matches (e.g. "Biostar A78MD socket specs AND AMD FX-6300 socket specs"). If the user just types a model name, check history.
    - "RAG_SEARCH": if the user asks about our prices, address, working hours, warranties.
    - "GENERAL": ONLY for casual greetings or small talk that has NO technical context.
    
    Output strictly valid JSON and nothing else.
    """
    
    messages = [{"role": "system", "content": sys_prompt}]
    if history:
        for h in history[-2:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": text})
        
    try:
        base_url = settings.meta.get("llm_base_url") if settings and settings.meta else None
        api_key = settings.meta.get("llm_api_key") if settings and settings.meta else None
        model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"
        
        response_text, usage_data = await chat(messages, model=model_name, temperature=0.1, base_url=base_url, api_key=api_key, return_usage=True, raise_error=True)
        
        clean_json = response_text.strip()
        if clean_json.startswith("```"):
            clean_json = "\n".join(clean_json.split("\n")[1:-1])
            
        data = json.loads(clean_json)
        data["usage"] = usage_data
        logger.info(f"Detected intent: {data}")
        return data
    except Exception as e:
        logger.error(f"Intent detection failed: {e}")
        return {"intent": "ERROR", "error": f"{type(e).__name__}: {str(e)}", "query": "", "usage": {"total_tokens": 0}}
