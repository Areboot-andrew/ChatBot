import json
import logging
from app.core.llm import chat

logger = logging.getLogger(__name__)

async def detect_intent(text: str, history: list = None) -> dict:
    """
    Analyzes the user's message and returns the intent and parameters.
    """
    sys_prompt = """
    You are an intent router for a computer repair shop bot.
    Analyze the user's message and return a JSON object with:
    - "intent": one of ["CHECK_REPAIR_STATUS", "WEB_SEARCH", "GENERAL"]
    - "query": if WEB_SEARCH, provide a precise English technical search query (e.g., "Intel i5-12400F specs socket"). Otherwise empty.
    
    Rules:
    - If the user asks about the status of their repair, return CHECK_REPAIR_STATUS.
    - If the user asks for exact technical specs, hardware compatibility, diagrams, socket types, power consumption, or specific model characteristics, return WEB_SEARCH.
    - Otherwise, return GENERAL.
    
    Output strictly valid JSON and nothing else.
    """
    
    messages = [{"role": "system", "content": sys_prompt}]
    if history:
        # Include a bit of history to understand context for the search query
        for h in history[-2:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
            
    # Add the current message
    messages.append({"role": "user", "content": text})
        
    try:
        response = await chat(messages, temperature=0.1)
        # Clean markdown if present
        clean_json = response.strip()
        if clean_json.startswith("```"):
            clean_json = "\n".join(clean_json.split("\n")[1:-1])
            
        data = json.loads(clean_json)
        logger.info(f"Detected intent: {data}")
        return data
    except Exception as e:
        logger.error(f"Intent detection failed: {e}. Raw response: {response if 'response' in locals() else 'None'}")
        return {"intent": "GENERAL", "query": ""}
