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
    Analyzes the user's message dynamically based on KnowledgeType rules.
    """
    res = await db.execute(select(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id, KnowledgeType.enabled == True))
    intents = res.scalars().all()
    
    res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res_s.scalars().first()
    
    rules = []
    codes = []
    for i in intents:
        codes.append(i.code)
        patterns_str = ", ".join(i.intent_patterns) if i.intent_patterns else "будь-які інші запити"
        rules.append(f'- "{i.code}": якщо користувач питає: {patterns_str}')
        
    codes_str = '", "'.join(codes)
    rules_str = "\n".join(rules)
    
    sys_prompt = f"""
    You are an intent router for a bot.
    Analyze the user's message and return a JSON object with:
    - "intent": one of ["{codes_str}"]
    - "query": a precise English technical search query if search is needed, otherwise empty.
    
    Rules:
    {rules_str}
    
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
        
        response_text, usage_data = await chat(messages, model=model_name, temperature=0.1, base_url=base_url, api_key=api_key, return_usage=True)
        
        clean_json = response_text.strip()
        if clean_json.startswith("```"):
            clean_json = "\n".join(clean_json.split("\n")[1:-1])
            
        data = json.loads(clean_json)
        data["usage"] = usage_data
        logger.info(f"Detected intent: {data}")
        return data
    except Exception as e:
        logger.error(f"Intent detection failed: {e}. Raw response: {response_text if 'response_text' in locals() else 'None'}")
        return {"intent": "ERROR", "error": str(e), "query": "", "usage": {"total_tokens": 0}}
