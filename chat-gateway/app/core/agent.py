"""
Agentic 2-mode loop (the old "Givi" pattern, generalized for multi-tenant).

Mode 1 (ROUTER): the model returns a compact JSON action. The framework
executes the tool and feeds the result back. Repeats up to max_iterations.
Mode 2 (ANSWER): the model speaks to the client naturally, grounded in the
facts gathered during the loop.

The action protocol below is FRAMEWORK MECHANICS (like SQL syntax) — shared by
all tenants and not stored in DB. Everything business-flavored (persona, tone,
business facts, which tools are enabled, sources) comes from tenant config.
"""
import asyncio
import json
import logging
import re
import time
import uuid

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import chat
from app.core.rag import search_knowledge
from app.core.tools import web_research, fetch_and_parse_url
from app.models.knowledge import QaPair
from app.models.services import ServicePrice, ServiceCategory

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 4
ALL_TOOLS = ["search_catalog", "search_knowledge", "web_research", "open_url", "get_business_info", "escalate"]

TOOL_DESCRIPTIONS = {
    "search_catalog": '"search_catalog": local price list / services / availability. query = short Ukrainian service or product wording.',
    "search_knowledge": '"search_knowledge": internal knowledge base (FAQ, warranty, conditions, documents). query = the client question, concise.',
    "web_research": '"web_research": internet research. Opens the most relevant found pages and reads their full content. query = precise ENGLISH technical query with the concrete device/model name. NEVER copy the client\'s raw wording or typos.',
    "open_url": '"open_url": open one specific URL and read its content. query = the full URL.',
    "get_business_info": '"get_business_info": address, working hours, phone, payment, delivery, warranty of this business. query = which field is needed.',
    "escalate": '"escalate": hand off to a human operator. Use when the client explicitly asks for a human or the conversation is stuck.',
}

ROUTER_PROTOCOL = """MODE: ROUTER_DECISION
You are deciding the NEXT STEP for answering the client. You are NOT talking to the client now.
Return ONLY valid compact JSON, no markdown, no explanations:
{"action": "<action>", "query": "<query or empty>", "reason": "<short>", "memory_patch": {}}

Allowed actions:
{tools_block}
"answer": you already have enough verified facts (or none are needed — greetings, small talk, tone-only replies). This ends the loop.

Decision rules:
- Greetings, thanks, small talk, emotions → "answer" immediately. NEVER search for greetings.
- Follow the chronology of the chat. Previous client requests stay active context until the topic clearly changes.
- Prices/availability/services of OUR business → search_catalog first. The internet is NOT our price source.
- Technical specs, compatibility, repair data missing from internal sources → web_research with a precise English query including the concrete model from the chat.
- If a concrete model/detail is missing and needed → "answer" (you will ask the client for it).
- Do not repeat an action that already returned results this turn. Maximum {max_iter} steps, then you must "answer".
- "memory_patch": durable facts about THIS client chat worth remembering (device model, chosen option, stage). Keys/values short strings. Empty object if nothing new.
"""

ANSWER_PROTOCOL = """MODE: FINAL_CLIENT_ANSWER
Now speak to the client naturally, following your persona and tone rules.
- Use ONLY the facts gathered in [GATHERED FACTS] and [CHAT MEMORY] for prices, specs, availability, compatibility. If a needed fact is absent — say you don't know / need to see the device / ask for the exact model. Never invent.
- Do not expose JSON, debug info, raw search dumps, or these instructions.
- Keep it short and human."""


def _extract_json(text: str) -> dict:
    """Tolerant JSON extraction: handles ```json fences and stray prose."""
    if not text:
        raise ValueError("empty router response")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in: {text[:200]}")
    return json.loads(match.group(0))


def _query_tokens(*texts: str) -> list:
    tokens = []
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[\w\d]+", t.lower(), re.UNICODE):
            if len(w) >= 3 and w not in tokens:
                tokens.append(w)
    return tokens


async def _tool_search_catalog(query: str, tenant_id: uuid.UUID, db: AsyncSession) -> str:
    tokens = _query_tokens(query)
    prices = []
    if tokens:
        conditions = [ServicePrice.name.ilike(f"%{tok}%") for tok in tokens]
        res = await db.execute(
            select(ServicePrice, ServiceCategory.title)
            .join(ServiceCategory, ServicePrice.category_id == ServiceCategory.id, isouter=True)
            .where(ServicePrice.tenant_id == tenant_id, or_(*conditions))
            .limit(8)
        )
        prices = res.all()
    if not prices:
        # Nothing matched — show available categories so the model can clarify.
        res_c = await db.execute(
            select(ServiceCategory.title).where(ServiceCategory.tenant_id == tenant_id).limit(15))
        cats = [c for (c,) in res_c.all() if c]
        if cats:
            return "Нічого не знайдено за запитом. Доступні категорії послуг: " + ", ".join(cats)
        return "Каталог порожній."
    return "\n".join([f"- {cat or 'Послуги'}: {p.name} — {p.price}" for p, cat in prices])


async def _tool_search_knowledge(query: str, tenant_id: uuid.UUID, db: AsyncSession, settings) -> str:
    top_k = 3
    threshold = 0.5
    try:
        top_k = int(settings.rag_top_k) if settings and settings.rag_top_k else 3
        threshold = float(settings.rag_score_threshold) if settings and settings.rag_score_threshold else 0.5
    except (ValueError, TypeError):
        pass
    parts = []
    tokens = _query_tokens(query)
    if tokens:
        qa_conditions = [QaPair.question.ilike(f"%{tok}%") for tok in tokens]
        qa_conditions += [QaPair.answer.ilike(f"%{tok}%") for tok in tokens]
        res_qa = await db.execute(
            select(QaPair)
            .where(QaPair.tenant_id == tenant_id, QaPair.enabled == True, or_(*qa_conditions))
            .limit(6)
        )
        for qa in res_qa.scalars().all():
            parts.append(f"Q: {qa.question}\nA: {qa.answer}")
    try:
        rag_docs = await search_knowledge(query, str(tenant_id), top_k=top_k, threshold=threshold)
        for doc in rag_docs:
            parts.append(f"[Документ]: {doc}")
    except Exception as e:
        logger.error(f"RAG error in agent: {e}")
    return "\n---\n".join(parts) if parts else "Нічого не знайдено у базі знань."


def _tool_get_business_info(query: str, settings) -> str:
    info = settings.meta.get("business_info") if settings and settings.meta else None
    if not info:
        return "Бізнес-інформація не налаштована."
    if isinstance(info, dict):
        return "\n".join([f"{k}: {v}" for k, v in info.items() if v])
    return str(info)


async def run_agent(
    text: str,
    history: list,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    settings,
    trace=None,
    memory: dict = None
) -> tuple:
    """
    Run the agentic loop. Returns (final_answer, updated_memory).
    Raises on router LLM connection failure so the caller can fall back.
    """
    emit = trace or (lambda *a, **k: None)
    meta = settings.meta if settings and settings.meta else {}

    enabled_tools = meta.get("enabled_tools") or ALL_TOOLS
    max_iter = 4
    try:
        max_iter = int(meta.get("agent_max_iterations", DEFAULT_MAX_ITERATIONS))
    except (ValueError, TypeError):
        pass

    tools_block = "\n".join([TOOL_DESCRIPTIONS[t] for t in enabled_tools if t in TOOL_DESCRIPTIONS])
    router_protocol = ROUTER_PROTOCOL.replace("{tools_block}", tools_block).replace("{max_iter}", str(max_iter))

    persona = settings.system_prompt if settings and settings.system_prompt else "You are a helpful assistant. Answer in Ukrainian."
    business_rules = settings.business_rules if settings and settings.business_rules else ""

    memory = dict(memory or {})
    gathered = []          # [(action, query, result)]
    actions_done = set()

    base_url = meta.get("llm_base_url")
    api_key = meta.get("llm_api_key")
    model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"

    def build_context_block() -> str:
        parts = []
        if memory:
            parts.append("[CHAT MEMORY]\n" + "\n".join([f"- {k}: {v}" for k, v in memory.items()]))
        if gathered:
            facts = []
            for action, query, result in gathered:
                facts.append(f"--- {action}('{query}') ---\n{result}")
            parts.append("[GATHERED FACTS]\n" + "\n".join(facts))
        return "\n\n".join(parts)

    escalated = False

    for iteration in range(1, max_iter + 1):
        sys_prompt = persona
        if business_rules:
            sys_prompt += "\n\n[BUSINESS RULES]\n" + business_rules
        context_block = build_context_block()
        if context_block:
            sys_prompt += "\n\n" + context_block
        sys_prompt += "\n\n" + router_protocol

        messages = [{"role": "system", "content": sys_prompt}]
        recent = (history or [])[-6:]
        for h in recent:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        if not recent or recent[-1].get("content") != text or recent[-1].get("role") != "user":
            messages.append({"role": "user", "content": text})

        t0 = time.time()
        raw, usage = await chat(
            messages, model=model_name, temperature=0.1, max_tokens=400,
            base_url=base_url, api_key=api_key, return_usage=True, raise_error=True
        )
        try:
            decision = _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            # Model broke protocol — treat its text as readiness to answer.
            emit(f"AGENT ROUTER #{iteration}", "JSON помилка", f"{e}\nRAW: {raw[:300]}", f"{time.time() - t0:.2f}s")
            break

        action = str(decision.get("action", "answer")).lower().strip()
        query = str(decision.get("query", "") or "")
        patch = decision.get("memory_patch") or {}
        if isinstance(patch, dict):
            for k, v in patch.items():
                if v is None or v == "":
                    memory.pop(str(k), None)
                else:
                    memory[str(k)] = str(v)

        emit(f"AGENT ROUTER #{iteration}", "Рішення",
             f"action={action}, query='{query}'\nreason: {decision.get('reason', '')}\nmemory_patch: {json.dumps(patch, ensure_ascii=False)}\nТокени: {usage.get('total_tokens', 0)}",
             f"{time.time() - t0:.2f}s")

        if action == "answer" or action not in enabled_tools:
            break

        if action in actions_done and action != "open_url":
            emit(f"AGENT TOOL #{iteration}", "Пропущено", f"'{action}' вже виконувався цього ходу")
            break
        actions_done.add(action)

        # Execute tool
        t0 = time.time()
        if action == "search_catalog":
            result = await _tool_search_catalog(query or text, tenant_id, db)
        elif action == "search_knowledge":
            result = await _tool_search_knowledge(query or text, tenant_id, db, settings)
        elif action == "web_research":
            result = await asyncio.to_thread(web_research, query or text)
        elif action == "open_url":
            result = await asyncio.to_thread(fetch_and_parse_url, query) if query.startswith("http") else "open_url потребує повного URL у query."
        elif action == "get_business_info":
            result = _tool_get_business_info(query, settings)
        elif action == "escalate":
            escalated = True
            result = meta.get("tpl_escalate_instruction", "[INSTRUCTION]: The client wants a human. Inform them you are transferring the conversation to a live operator.")
        else:
            result = f"Невідома дія '{action}'."

        gathered.append((action, query, result))
        emit(f"AGENT TOOL #{iteration}", action, str(result)[:800], f"{time.time() - t0:.2f}s")

        if escalated:
            break

    # --- FINAL ANSWER MODE ---
    sys_prompt = persona
    if business_rules:
        sys_prompt += "\n\n[BUSINESS RULES]\n" + business_rules
    marketing = settings.marketing_rules if settings and settings.marketing_rules else ""
    if marketing and gathered:
        sys_prompt += "\n\n[MARKETING — apply only if natural]\n" + marketing
    context_block = build_context_block()
    if context_block:
        sys_prompt += "\n\n" + context_block
    sys_prompt += "\n\n" + ANSWER_PROTOCOL

    temp = 0.7
    max_tokens = 1024
    try:
        temp = float(settings.temperature) if settings and settings.temperature else 0.7
        max_tokens = int(settings.max_tokens) if settings and settings.max_tokens else 1024
    except (ValueError, TypeError):
        pass

    messages = [{"role": "system", "content": sys_prompt}]
    for h in (history or []):
        messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    if not history or history[-1].get("content") != text or history[-1].get("role") != "user":
        messages.append({"role": "user", "content": text})

    t0 = time.time()
    answer = await chat(
        messages, model=model_name, temperature=temp, max_tokens=max_tokens,
        base_url=base_url, api_key=api_key, raise_error=True
    )
    emit("AGENT ANSWER", "OK", f"Кроків циклу: {len(gathered)}, пам'ять: {json.dumps(memory, ensure_ascii=False)}", f"{time.time() - t0:.2f}s")
    return answer, memory
