import json
import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.llm import chat
from app.config import normalize_lmstudio_url
from app.models.tenant import KnowledgeType, BotSetting

logger = logging.getLogger(__name__)


async def detect_intent(text: str, history: list, tenant_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Analyzes the user's message and routes it to the correct subsystem.
    The router prompt is built dynamically from KnowledgeType records in the DB.
    """
    # Load tenant LLM settings
    res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res_s.scalars().first()

    # Load all enabled KnowledgeType records for this tenant, ordered by priority
    res_kt = await db.execute(
        select(KnowledgeType)
        .where(KnowledgeType.tenant_id == tenant_id, KnowledgeType.enabled == True)
        .order_by(KnowledgeType.priority)
    )
    knowledge_types = res_kt.scalars().all()

    # Build list of valid intent codes and their descriptions
    intent_lines = []
    intent_codes = []
    for kt in knowledge_types:
        intent_codes.append(kt.code)
        description = kt.label or kt.code
        # Use intent_patterns from meta as additional hints if available
        patterns_hint = ""
        if kt.intent_patterns:
            patterns_hint = f" (e.g. {', '.join(kt.intent_patterns[:3])})"
        intent_lines.append(f'- "{kt.code}": {description}{patterns_hint}')

    # Always add GENERAL as fallback
    if "GENERAL" not in intent_codes:
        intent_codes.append("GENERAL")
        intent_lines.append('- "GENERAL": General conversation, greetings, small talk, or anything that does not match the intents above.')

    all_codes_str = json.dumps(intent_codes)
    intents_block = "\n".join(intent_lines)

    # Router rules are overridable per tenant via meta (no-hardcode project rule).
    default_router_rules = """Rules:
- Choose the most specific intent that matches the user's request.
- If the user's message is a casual greeting, thanks, small talk, or off-topic chatter, use "GENERAL" and leave "query" empty. NEVER generate a search query for greetings or emotions.
- Query rules (when a search IS needed):
  * Write the query yourself in clean, precise ENGLISH for technical topics (specs, compatibility, repair data). NEVER copy the client's raw wording, typos, or transliteration.
  * Always include the concrete device/model name. Pull the model from the conversation history if the client mentioned it earlier.
  * For local price/stock/services queries use short Ukrainian product wording.
  * No generic queries like "tv repair info". Be specific: "LG 65UN73006LA panel backlight replacement cost".
- Output strictly valid JSON and nothing else."""
    router_rules = (settings.meta.get("tpl_router_rules") if settings and settings.meta else None) or default_router_rules

    sys_prompt = f"""You are an intent router for a customer-facing chatbot.
Analyze the user's message IN THE CONTEXT of the conversation history.
Return a JSON object with:
- "intent": one of {all_codes_str}
- "query": a precise search query relevant to the detected intent (if a search is needed), otherwise empty string.

Available intents:
{intents_block}

{router_rules}"""

    messages = [{"role": "system", "content": sys_prompt}]
    if history:
        for h in history[-2:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    messages.append({"role": "user", "content": text})

    try:
        base_url = normalize_lmstudio_url(settings.meta.get("llm_base_url")) if settings and settings.meta else None
        api_key = settings.meta.get("llm_api_key") if settings and settings.meta else None
        model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"

        response_text, usage_data = await chat(
            messages, model=model_name, temperature=0.1,
            base_url=base_url, api_key=api_key,
            return_usage=True, raise_error=True
        )

        clean_json = response_text.strip()
        if clean_json.startswith("```"):
            clean_json = "\n".join(clean_json.split("\n")[1:-1])

        data = json.loads(clean_json)
        
        # Normalize intent casing against valid intent_codes
        raw_intent = data.get("intent", "GENERAL")
        matched_intent = "GENERAL"
        for code in intent_codes:
            if code.lower() == str(raw_intent).lower():
                matched_intent = code
                break
        data["intent"] = matched_intent
        
        data["usage"] = usage_data
        logger.info(f"Detected intent: {data}")
        return data
    except Exception as e:
        logger.error(f"Intent detection failed: {e}")
        return {"intent": "ERROR", "error": f"{type(e).__name__}: {str(e)}", "query": "", "usage": {"total_tokens": 0}}
