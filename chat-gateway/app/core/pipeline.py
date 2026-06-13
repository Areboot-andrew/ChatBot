import asyncio
import re
import time
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
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

# spec §9.5: knowledge budget per request; rows of structured data injected at most
MAX_PRICE_ROWS = 8
MAX_QA_ROWS = 10


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


def _query_tokens(*texts: str) -> list:
    """Extract meaningful search tokens (3+ chars, words/digits) from texts."""
    tokens = []
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[\w\d]+", t.lower(), re.UNICODE):
            if len(w) >= 3 and w not in tokens:
                tokens.append(w)
    return tokens


def _noop_trace(step: str, status: str, details: str, duration: str = "-"):
    pass


async def process_message_pipeline(
    text: str,
    history: list,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    trace=None,
    chat_key: str = None
) -> str:
    """
    Core pipeline to process an incoming message and return the LLM response.

    Engine is per-tenant (meta.engine): "agent" (default) runs the agentic
    action loop; "classic" runs the one-shot intent router. Agent errors fall
    back to classic automatically.

    `trace` is an optional callback(step, status, details, duration) used by the
    admin sandbox to visualize every step. The same pipeline serves all channels.
    `chat_key` (e.g. "telegram:12345") enables durable per-chat agent memory.
    """
    emit = trace or _noop_trace

    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res.scalars().first()

    # --- AGENT ENGINE (Givi-style action loop) ---
    engine = (settings.meta.get("engine") if settings and settings.meta else None) or "agent"
    if engine == "agent":
        from app.core.agent import run_agent
        from app.core.history import MemoryManager
        memory = await MemoryManager.get_memory(chat_key) if chat_key else {}
        if memory.get("_session_banned") == "1":
            emit("SESSION", "Заблоковано", "Ця сесія забанена; відповідь приглушено.")
            return ""
        try:
            was_banned = memory.get("_session_banned") == "1"
            answer, new_memory = await run_agent(
                text, history, tenant_id, db, settings, trace=trace, memory=memory
            )
            if chat_key:
                await MemoryManager.save_memory(chat_key, new_memory)
                if not was_banned and new_memory.get("_session_banned") == "1":
                    from app.core.bans import record_session_ban
                    await record_session_ban(db, tenant_id, chat_key, text)
            return answer
        except Exception as e:
            logger.error(f"Agent engine failed, falling back to classic: {e}")
            emit("AGENT ENGINE", "Помилка → класичний режим", str(e))

    # --- CLASSIC ENGINE (one-shot intent router) ---
    # 1. Intent Recognition
    t0 = time.time()
    intent_data = await detect_intent(text, history, tenant_id, db)
    intent = intent_data.get("intent", "GENERAL")
    search_query = intent_data.get("query", "")
    emit("LLM ROUTER: DECISION", "OK" if intent != "ERROR" else "Помилка",
         f"intent={intent}, query='{search_query}'\nТокени: {intent_data.get('usage', {}).get('total_tokens', 0)}",
         f"{time.time() - t0:.2f}s")

    # Read DB settings with safe defaults
    fallback_text = (settings.fallback_text if settings and settings.fallback_text else DEFAULT_FALLBACK_TEXT)
    rag_top_k = _safe_int(settings.rag_top_k if settings else None, 3)
    rag_threshold = _safe_float(settings.rag_score_threshold if settings else None, 0.5)
    max_tokens = _safe_int(settings.max_tokens if settings else None, 1024)

    if intent == "ERROR":
        emit("SYSTEM", "Фолбек", f"Роутер впав: {intent_data.get('error', '')}")
        return fallback_text

    qa_facts = []
    rag_docs = []
    prices = []
    sys_prompt_addition = ""

    # 2. Fetch KnowledgeType logic
    knowledge_type = None
    if intent != "GENERAL":
        from app.models.tenant import KnowledgeType
        res_kt = await db.execute(select(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id, KnowledgeType.code == intent))
        knowledge_type = res_kt.scalars().first()

    # Check that the knowledge type is enabled before using it
    if knowledge_type and not knowledge_type.enabled:
        logger.warning(f"KnowledgeType '{intent}' is disabled for tenant {tenant_id}, falling back.")
        knowledge_type = None

    # spec §9.5 step 0: smalltalk/greetings answer from persona only — no knowledge,
    # no waterfall, no web search. GENERAL never reaches any handler.
    if intent == "GENERAL":
        handler = "smalltalk"
    else:
        handler = knowledge_type.handler if knowledge_type else "fallback"
    emit("SYSTEM LOGIC (Маршрутизація)", "Вибір гілки", f"handler={handler}")

    # 3. Execute Handler Logic
    if handler == "smalltalk":
        pass  # persona + history only (spec §9.5: lazy context assembly)

    elif handler == "qa_handler":
        # Targeted price lookup (spec M4): match rows against the router query /
        # user text instead of dumping the whole price list into the prompt.
        tokens = _query_tokens(search_query, text)
        t0 = time.time()
        if tokens:
            conditions = [ServicePrice.name.ilike(f"%{tok}%") for tok in tokens]
            res_price = await db.execute(
                select(ServicePrice)
                .where(ServicePrice.tenant_id == tenant_id, or_(*conditions))
                .limit(MAX_PRICE_ROWS)
            )
            prices = res_price.scalars().all()
        if not prices:
            # No keyword match — give the model a small sample so it can name
            # categories, but never the full list.
            res_price = await db.execute(
                select(ServicePrice).where(ServicePrice.tenant_id == tenant_id).limit(MAX_PRICE_ROWS)
            )
            prices = res_price.scalars().all()
        emit("SQL DATABASE (PostgreSQL)", "OK" if prices else "Пусто",
             f"Прайси: {len(prices)} рядків (таргетовано по: {tokens[:6]})", f"{time.time() - t0:.2f}s")
        if prices:
            tpl_price_data = settings.meta.get("tpl_price_data", "\n[Price List Data]:\n") if settings and settings.meta else "\n[Price List Data]:\n"
            sys_prompt_addition = tpl_price_data + "\n".join([f"- {p.name}: {p.price}" for p in prices])

        # Targeted QA: only pairs whose question/answer matches the request.
        if tokens:
            qa_conditions = [QaPair.question.ilike(f"%{tok}%") for tok in tokens]
            qa_conditions += [QaPair.answer.ilike(f"%{tok}%") for tok in tokens]
            res_qa = await db.execute(
                select(QaPair)
                .where(QaPair.tenant_id == tenant_id, QaPair.enabled == True, or_(*qa_conditions))
                .limit(MAX_QA_ROWS)
            )
            qa_facts = res_qa.scalars().all()
        emit("SQL DATABASE (QA Pairs)", "OK" if qa_facts else "Пусто", f"QA: {len(qa_facts)} пар")

        # Qdrant RAG
        t0 = time.time()
        try:
            rag_docs = await search_knowledge(text, str(tenant_id), top_k=rag_top_k, threshold=rag_threshold)
        except Exception as e:
            logger.error(f"RAG search error: {e}")
            rag_docs = []
        emit("VECTOR DB (Qdrant RAG)", "OK" if rag_docs else "Пусто",
             f"Знайдено фрагментів: {len(rag_docs)}", f"{time.time() - t0:.2f}s")

    elif handler == "web_search_handler":
        if search_query:
            t0 = time.time()
            search_result = await asyncio.to_thread(search_internet, search_query, 3)
            emit("EXTERNAL API (Web Search)", "OK", f"Query: {search_query}\n{search_result[:500]}", f"{time.time() - t0:.2f}s")
            tpl_web_search = settings.meta.get("tpl_web_search", "\n[Web Search Results for '{query}']:\n") if settings and settings.meta else "\n[Web Search Results for '{query}']:\n"
            sys_prompt_addition = tpl_web_search.replace("{query}", search_query) + search_result

    elif handler == "site_search":
        target_url = knowledge_type.meta.get("target_url") if knowledge_type.meta else ""
        if target_url:
            t0 = time.time()
            if "{query}" in target_url:
                from urllib.parse import quote
                # Fetch custom URL
                final_url = target_url.replace("{query}", quote(search_query or text))
                from app.core.tools import fetch_and_parse_url
                site_content = await asyncio.to_thread(fetch_and_parse_url, final_url)
                emit("EXTERNAL API (Site Fetch)", "OK", f"URL: {final_url}", f"{time.time() - t0:.2f}s")
                tpl_site_search = settings.meta.get("tpl_site_search", "\n[Site Search Results ({url})]:\n") if settings and settings.meta else "\n[Site Search Results ({url})]:\n"
                sys_prompt_addition = tpl_site_search.replace("{url}", final_url) + site_content
            else:
                # Use DuckDuckGo with site:
                search_result = await asyncio.to_thread(search_internet, f"site:{target_url} {search_query or text}", 3)
                emit("EXTERNAL API (Site Search)", "OK", f"site:{target_url} {search_query or text}", f"{time.time() - t0:.2f}s")
                tpl_site_search = settings.meta.get("tpl_site_search", "\n[Site Search Results ({url})]:\n") if settings and settings.meta else "\n[Site Search Results ({url})]:\n"
                sys_prompt_addition = tpl_site_search.replace("{url}", target_url) + search_result

    elif handler == "escalate":
        tpl_escalate = settings.meta.get("tpl_escalate_instruction", "\n[INSTRUCTION]: The user wants to speak with a human agent. Inform them that you are transferring the conversation to a live operator.") if settings and settings.meta else "\n[INSTRUCTION]: The user wants to speak with a human agent. Inform them that you are transferring the conversation to a live operator."
        sys_prompt_addition = tpl_escalate
        emit("SYSTEM", "Ескалація", "Передача на оператора")

    elif handler == "fallback":
        # Substantive intent without a configured handler: try own knowledge first.
        t0 = time.time()
        try:
            rag_docs = await search_knowledge(text, str(tenant_id), top_k=rag_top_k, threshold=rag_threshold)
        except Exception as e:
            logger.error(f"RAG search error: {e}")
            rag_docs = []
        emit("VECTOR DB (Qdrant RAG)", "OK" if rag_docs else "Пусто",
             f"Знайдено фрагментів: {len(rag_docs)}", f"{time.time() - t0:.2f}s")

        # Waterfall Logic — only when the router produced a real search query.
        # Never feed raw user text (greetings, typos) to a search engine.
        if not rag_docs and search_query:
            fallback_sites = settings.meta.get("fallback_sites", "") if settings and settings.meta else ""
            found_in_waterfall = False

            # Step 1: Trusted Sites
            if fallback_sites:
                sites = [s.strip() for s in fallback_sites.split(",")]
                sites_query = " OR ".join([f"site:{s}" for s in sites])
                t0 = time.time()
                search_result = await asyncio.to_thread(search_internet, f"({sites_query}) {search_query}", 3)
                emit("EXTERNAL API (Trusted Sites)", "OK", f"Query: {search_query}\nSites: {fallback_sites}", f"{time.time() - t0:.2f}s")

                if search_result and "no results" not in search_result.lower():
                    tpl_trusted = settings.meta.get("tpl_trusted_search", "\n[Trusted Sites Data ({sites})]:\n") if settings and settings.meta else "\n[Trusted Sites Data ({sites})]:\n"
                    sys_prompt_addition = tpl_trusted.replace("{sites}", fallback_sites) + search_result
                    found_in_waterfall = True

            # Step 2: General Internet
            if not found_in_waterfall:
                t0 = time.time()
                search_result = await asyncio.to_thread(search_internet, search_query, 3)
                emit("EXTERNAL API (Web Search)", "OK", f"Query: {search_query}\n{search_result[:500]}", f"{time.time() - t0:.2f}s")
                tpl_general = settings.meta.get("tpl_general_search", "\n[General Web Search Results]:\n") if settings and settings.meta else "\n[General Web Search Results]:\n"
                sys_prompt_addition = tpl_general + search_result
        elif not rag_docs:
            emit("SYSTEM", "Пропущено", "Роутер не дав пошукового запиту — в інтернет не йдемо")

    # 4. Build Prompt (spec §9.5: inject only what this route actually fetched)
    has_knowledge_context = bool(qa_facts or rag_docs or sys_prompt_addition)
    sys_prompt = build_system_prompt(settings, qa_facts, rag_docs, include_grounding=has_knowledge_context)
    sys_prompt += sys_prompt_addition

    temp = float(settings.temperature) if settings and settings.temperature else 0.7

    messages = [{"role": "system", "content": sys_prompt}]

    for h in (history or []):
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    # Make sure the current message is present exactly once (some channels pass
    # history without it, the sandbox passes history including it).
    if not history or history[-1].get("content") != text or history[-1].get("role") != "user":
        messages.append({"role": "user", "content": text})

    # 5. Generate LLM Response
    base_url = settings.meta.get("llm_base_url") if settings and settings.meta else None
    api_key = settings.meta.get("llm_api_key") if settings and settings.meta else None
    model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"

    emit("LLM (Final Answer)", "Генерація...",
         f"Модель: {model_name}, temp={temp}, max_tokens={max_tokens}\nРозмір системного промпта: {len(sys_prompt)} симв.")
    t0 = time.time()
    try:
        response_text = await chat(
            messages, model=model_name, temperature=temp,
            max_tokens=max_tokens,
            base_url=base_url, api_key=api_key
        )
        emit("LLM (Final Answer)", "OK", f"Відповідь: {len(response_text)} симв.", f"{time.time() - t0:.2f}s")
        return response_text
    except Exception as e:
        logger.error(f"LLM Generation error: {e}")
        emit("LLM (Final Answer)", "Помилка", str(e), f"{time.time() - t0:.2f}s")
        return fallback_text
