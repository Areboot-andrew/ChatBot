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

DEFAULT_FALLBACK_TEXT = "Sorry, an error occurred while processing your request. Please try again later."


def _safe_int(value, default: int) -> int:
    """Safely convert a string/None to int, returning default on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value, default: float) -> float:
    """Safely convert a string/None to float, returning default on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


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
    intent_data = await detect_intent(text, history, tenant_id, db)
    intent = intent_data.get("intent", "GENERAL")
    search_query = intent_data.get("query", "")
    
    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res.scalars().first()

    # Read DB settings with safe defaults
    fallback_text = (settings.fallback_text if settings and settings.fallback_text else DEFAULT_FALLBACK_TEXT)
    rag_top_k = _safe_int(settings.rag_top_k if settings else None, 3)
    rag_threshold = _safe_float(settings.rag_score_threshold if settings else None, 0.5)
    max_tokens = _safe_int(settings.max_tokens if settings else None, 1024)

    if intent == "ERROR":
        return fallback_text
        
    qa_facts = []
    rag_docs = []
    prices = []
    sys_prompt_addition = ""
    
    # 2. Fetch KnowledgeType logic
    knowledge_type = None
    if intent != "ERROR" and intent != "GENERAL":
        from app.models.tenant import KnowledgeType
        res_kt = await db.execute(select(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id, KnowledgeType.code == intent))
        knowledge_type = res_kt.scalars().first()

    # Check that the knowledge type is enabled before using it
    if knowledge_type and not knowledge_type.enabled:
        logger.warning(f"KnowledgeType '{intent}' is disabled for tenant {tenant_id}, falling back.")
        knowledge_type = None

    handler = knowledge_type.handler if knowledge_type else "fallback"
    
    # 3. Execute Handler Logic
    if handler == "qa_handler":
        # Fetching prices
        res_price = await db.execute(select(ServicePrice).where(ServicePrice.tenant_id == tenant_id).limit(100))
        prices = res_price.scalars().all()
        if prices:
            sys_prompt_addition = "\n[Price List Data]:\n" + "\n".join([f"- {p.name}: {p.price}" for p in prices])
            
        # SQL QA
        res_qa = await db.execute(select(QaPair).where(QaPair.tenant_id == tenant_id).limit(50))
        qa_facts = res_qa.scalars().all()
        
        # Qdrant RAG
        try:
            rag_docs = await search_knowledge(text, str(tenant_id), top_k=rag_top_k, threshold=rag_threshold)
        except Exception as e:
            logger.error(f"RAG search error: {e}")
            rag_docs = []
            
    elif handler == "web_search_handler":
        if search_query:
            search_result = search_internet(search_query, max_results=3)
            sys_prompt_addition = f"\n[Web Search Results for '{search_query}']:\n{search_result}"
            
    elif handler == "site_search":
        target_url = knowledge_type.meta.get("target_url") if knowledge_type.meta else ""
        if target_url:
            if "{query}" in target_url:
                from urllib.parse import quote
                # Fetch custom URL
                final_url = target_url.replace("{query}", quote(search_query or text))
                from app.core.tools import fetch_and_parse_url
                site_content = fetch_and_parse_url(final_url)
                sys_prompt_addition = f"\n[Site Search Results ({final_url})]:\n{site_content}"
            else:
                # Use DuckDuckGo with site:
                search_result = search_internet(f"site:{target_url} {search_query or text}", max_results=3)
                sys_prompt_addition = f"\n[Site Search Results ({target_url})]:\n{search_result}"
                
    elif handler == "escalate":
        sys_prompt_addition = "\n[INSTRUCTION]: The user wants to speak with a human agent. Inform them that you are transferring the conversation to a live operator."
        
    elif handler == "fallback" or handler == "qa_handler":
        # Check Qdrant just in case, even for fallback
        try:
            rag_docs = await search_knowledge(text, str(tenant_id), top_k=rag_top_k, threshold=rag_threshold)
        except Exception as e:
            logger.error(f"RAG search error: {e}")
            rag_docs = []
            
        # Waterfall Logic
        if not rag_docs and not prices and not qa_facts:
            fallback_sites = settings.meta.get("fallback_sites", "") if settings.meta else ""
            found_in_waterfall = False
            
            # Step 1: Trusted Sites
            if fallback_sites:
                sites = [s.strip() for s in fallback_sites.split(",")]
                sites_query = " OR ".join([f"site:{s}" for s in sites])
                search_result = search_internet(f"({sites_query}) {search_query or text}", max_results=3)
                
                if search_result and "no results" not in search_result.lower():
                    sys_prompt_addition = f"\n[Trusted Sites Data ({fallback_sites})]:\n{search_result}"
                    found_in_waterfall = True
                    
            # Step 2: General Internet
            if not found_in_waterfall:
                search_result = search_internet(f"{search_query or text}", max_results=3)
                sys_prompt_addition = f"\n[General Web Search Results]:\n{search_result}"
                
    # 4. Build Prompt
    sys_prompt = build_system_prompt(settings, qa_facts, rag_docs)
    sys_prompt += sys_prompt_addition
    
    temp = float(settings.temperature) if settings and settings.temperature else 0.7
    
    messages = [{"role": "system", "content": sys_prompt}]
    
    if history:
        for h in history:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    else:
        messages.append({"role": "user", "content": text})
        
    # 5. Generate LLM Response
    base_url = settings.meta.get("llm_base_url") if settings and settings.meta else None
    api_key = settings.meta.get("llm_api_key") if settings and settings.meta else None
    model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"
    
    try:
        response_text = await chat(
            messages, model=model_name, temperature=temp,
            max_tokens=max_tokens,
            base_url=base_url, api_key=api_key
        )
        return response_text
    except Exception as e:
        logger.error(f"LLM Generation error: {e}")
        return fallback_text
