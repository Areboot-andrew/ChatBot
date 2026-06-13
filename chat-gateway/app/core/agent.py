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

DEFAULT_MAX_ITERATIONS = 5
ALL_TOOLS = ["list_categories", "search_catalog", "search_knowledge", "search_parts", "web_research", "open_url", "get_business_info", "escalate"]

TOOL_DESCRIPTIONS = {
    "list_categories": '"list_categories": list our service categories with counts only (cheap, no prices). Use first to see what areas we cover, then drill down with search_catalog.',
    "search_catalog": '"search_catalog": OUR local price list / services. query = a service name OR a category name. It drills down step by step: by service name, else the matching category\'s services, else the category list. Search again with a narrower/different word to dig deeper instead of loading everything.',
    "search_parts": '"search_parts": MARKET price of a spare part (display module, battery, etc.) from external parts suppliers — used when the part/model is NOT in our catalog. This is a THIRD-PARTY price reference, NOT our price. query = the part + exact model. Try both Ukrainian and English wording.',
    "search_knowledge": '"search_knowledge": internal knowledge base (FAQ, warranty, conditions, documents). query = the client question, concise.',
    "web_research": '"web_research": internet research. Opens the most relevant found pages and reads their full content. query = precise ENGLISH technical query with the concrete device/model name. NEVER copy the client\'s raw wording or typos.',
    "open_url": '"open_url": open one specific URL and read its content. query = the full URL.',
    "get_business_info": '"get_business_info": address, working hours, phone, payment, delivery, warranty of this business. query = which field is needed.',
    "escalate": '"escalate": hand off to a human operator. Use when the client explicitly asks for a human or the conversation is stuck.',
}

# ENGINE MECHANICS — hardcoded by design (JSON action format, the loop). This is
# syntax, not business logic. The {decision_rules} block below is EDITABLE per
# tenant (meta.agent_decision_rules) — it controls HOW to act / where to get data.
ROUTER_PROTOCOL = """MODE: ROUTER_DECISION
You are deciding the NEXT STEP for answering the client. You are NOT talking to the client now.
Return ONLY valid compact JSON, no markdown, no explanations:
{"action": "<action>", "query": "<query or empty>", "reason": "<short>", "memory_patch": {}}

Allowed actions:
{tools_block}
"answer": you already have enough verified facts (or none are needed — greetings, small talk, tone-only replies). This ends the loop.

{decision_rules}

Mechanics: do not repeat the same action+query twice. Maximum {max_iter} steps, then you must "answer". "memory_patch": durable facts about THIS chat (device model, chosen option, stage); empty object if nothing new.
Format examples ONLY (placeholders — always use the CLIENT'S real words/device):
Client: <greeting> -> {"action":"answer","query":"","reason":"greeting","memory_patch":{}}
Client: <do you service X?> -> {"action":"search_catalog","query":"<X>","reason":"check service","memory_patch":{}}
Client: <price of service for device Y> -> {"action":"search_catalog","query":"<service Y>","reason":"price lookup","memory_patch":{}}
Client: <working hours / address?> -> {"action":"get_business_info","query":"hours","reason":"business fact","memory_patch":{}}
Answer ONLY about the device the CLIENT mentioned. Do not introduce a different device."""


# EDITABLE decision rules (meta.agent_decision_rules) — HOW to act / where to get
# data / what data we have. Default in English; tenant can fully override in panel.
DEFAULT_DECISION_RULES = """Decision rules — choose the next action from the CONVERSATION CONTEXT. Use a tool ONLY when you actually need that data. Do not pile up context.
- SELF-CHECK at EVERY step before acting: "Do I clearly understand what the client wants right now?" If there is ANY doubt — vague wording, unclear which device/service, missing model, contradictory or off-topic context — then action "answer" with ONE short clarifying question. NEVER search or assume on an unclear request. Put your confidence in "reason".
- Only when the intent is clear do you pick a tool and search the relevant source.
- Greeting / small talk / emotion → answer (no search).
- The client describes a broken device or wants to bring it in, and did NOT ask a price → if unsure, search_catalog once to check we service this TYPE; then answer: confirm "так, ремонтуємо" (if yes) and ask WHAT exactly is wrong / the model. Do NOT quote any prices — they weren't asked.
- First understand WHAT is broken (the symptom/part). Only when the concrete service is clear AND the client asks about price → search_catalog for THAT one service and give a single orientation range. NEVER dump the whole price list / all prices.
- "do you repair X?" → search_catalog to confirm → answer yes/no, no prices.
- If the device is UNKNOWN / not in our catalog → web_research to learn what it is; if it belongs to a category we repair (e.g. small home appliance / дрібна побутова) → offer to bring it in for diagnostics.
- Price of a SPARE PART not in our catalog → search_parts / web_research for the market price.
- STEP BY STEP: if one source returns nothing, try the NEXT relevant source. If nothing is found anywhere, answer honestly «не знайшов / треба глянути на місці» — NEVER invent prices or links.
- get_business_info for our address/hours/payment/delivery. memory_patch: remember the device model / stage so you don't re-ask."""

# Default final-answer style. Editable per tenant via meta.answer_style
# (Налаштування → «Стиль відповіді»). This is TONE, not engine mechanics.
DEFAULT_ANSWER_STYLE = """--- WRITE THE CLIENT REPLY ---
Reply in Ukrainian, address the client formally ("Ви"), in your persona's voice. A real person is on the other side.
- SHORT: 1, maximum 2 sentences. One simple question at a time. Lead the conversation step by step.
- Mentally fix the client's typos/slang and understand the intent; never comment on their spelling.
- Do NOT list possible faults/options and do NOT add extras ("привозьте", "діагностика безкоштовна", addresses, prices) until that step is actually relevant.
- "do you repair/have X?" → just "Так, ремонтуємо/є. Що саме?" — nothing more.
- Prices/links ONLY from the gathered facts and ONLY when the client asked. Give ONE relevant price, never the whole list. Exact price after inspection.
- If a part price/link was NOT found: "Зараз не можу знайти потрібну запчастину. Як привезете — інженер гляне точну модель запчастини і погодимо ціну ремонту." NEVER invent prices, shops or links.
- Never expose internal tools, catalog/category names, JSON, English, or "[...]" markers."""


# External part-price logic/labelling — default; editable via meta.parts_instruction.
DEFAULT_PARTS_INSTRUCTION = (
    "When the part is not in our price list: search the parts sites first, then google. "
    "HOW TO PRESENT TO THE CLIENT: if prices were found, say it naturally — «глянув у постачальників, "
    "ціни приблизно такі: ...» — and give 1-2 source links (URLs) from the data if available. Add our "
    "labour from the catalog. Present the part price as an EXTERNAL/market price (bought separately); the "
    "exact price the master gives after inspection. IF NO PRICE WAS FOUND or the search was empty — do NOT "
    "invent numbers: say «точної ціни зараз не знайду, але як привезете прилад — на місці підберемо»."
)


_JUNK_PATTERNS = [
    r"(?im)^\s*we already gave final\.?\s*$",
    r"(?i)\bwe already gave final\.?",
    r"(?im)^\s*MODE:.*$",
    r"(?im)^\s*\{.*\"action\".*\}\s*$",
    r"(?im)^\s*(reason|action|memory_patch|query)\s*[:=].*$",
    # leaked placeholder ranges the model copied from instructions
    r"(?i)від\s*X\s*грн\s*до\s*Y\s*грн",
    r"(?i)від\s*X\s*до\s*Y(\s*грн)?",
    r"(?i)\bвід\s*[XY]\s*грн\b",
    r"(?i)\b[XY]\s*грн\s*(до|–|-)\s*[XY]\s*грн\b",
]


def _clean_answer(text: str) -> str:
    """Strip leaked router/service artefacts (English meta, JSON) from the
    client-facing reply — safety net for small models."""
    if not text:
        return text
    import re as _re
    out = text
    for pat in _JUNK_PATTERNS:
        out = _re.sub(pat, "", out)
    # collapse leftover blank lines
    out = _re.sub(r"\n{3,}", "\n\n", out).strip()
    return out or text


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


_GREETING_WORDS = {
    "привіт", "привітик", "прив", "вітаю", "добрий", "доброго", "здрастуйте", "здоров",
    "хай", "дякую", "дякс", "спасибі", "ок", "окей", "окк", "бувай", "па", "пока",
    "hello", "hi", "hey", "thanks", "ok", "okay", "bye",
}
_SUBSTANTIVE_TRIGGERS = (
    "?", "ремонт", "лагод", "цін", "скільки", "кошт", "вартіст", "робите", "робити",
    "ремонтуєте", "маєте", "можете", "берете", "беретесь", "гарант", "адрес",
    "години", "графік", "працює", "де ви", "послуг", "запчаст", "діагност",
)


_BUSINESS_INFO_TRIGGERS = (
    "годин", "графік", "режим робот", "коли працює", "коли ви працює", "до котрої",
    "з котрої", "вихідн", "адрес", "де ви", "де знаход", "як вас знайти", "куди їхати",
    "оплат", "оплачу", "розрахун", "карт", "готівк", "наложк", "телефон", "номер",
    "контакт", "звʼязат", "зв'язат", "гарант", "доставк", "відправк", "пошт",
    # days of week + visit intent — must be checked against the schedule
    "понеділок", "вівторок", "середу", "середа", "четвер", "пʼятниц", "п'ятниц",
    "субот", "неділ", "вихідни", "свят",
    "завтра", "сьогодні", "приїд", "заїд", "підійд", "зайд", "підвезу", "привезу",
    "буду", "коли можна", "коли підійти", "коли приходити", "о котрій",
)


def _looks_business_info(text: str) -> bool:
    t = (text or "").lower()
    return any(tr in t for tr in _BUSINESS_INFO_TRIGGERS)


_PART_WORDS = ("матриц", "дисплей", "екран", "модул", "акумулятор", "батаре", "акб",
               "скло", "тачскрін", "запчаст", "корпус", "камер", "динамік", "роз'єм",
               "шлейф", "плат", "крышк", "кришк")
_BRANDS = ("iphone", "айфон", "samsung", "самсунг", "xiaomi", "ксіомі", "сяомі",
           "huawei", "хуавей", "redmi", "poco", "oppo", "realme", "lg", "sony",
           "nokia", "motorola", "honor", "tecno", "infinix", "pixel", "macbook", "ipad")


def _looks_specific_part_query(text: str, history: list = None) -> bool:
    """Asks for a part/price of a CONCRETE model (brand + part, or brand + number).
    Such queries need the market price even if a generic catalog service exists."""
    blob = (text or "").lower()
    if history:
        for h in history[-4:]:
            blob += " " + str(h.get("content", "")).lower()
    has_brand = any(b in blob for b in _BRANDS)
    has_part = any(p in blob for p in _PART_WORDS)
    has_number = bool(re.search(r"\b\d{1,4}\b", blob))
    # brand + (part or a model number) → specific enough to need market price
    return has_brand and (has_part or has_number)


_PRICE_WORDS = ("ціна", "цін", "коштує", "вартіст", "почому", "за скільки",
                "скільки кошт", "скільки буде", "скільки за", "прайс", "ціну")
_CAPABILITY_WORDS = ("ремонтуєте", "чи робите", "робите ви", "берете в ремонт",
                     "беретесь", "можете полагодити", "можете відремонт", "чи лагодите",
                     "маєте послугу", "ви лагодите", "ремонтуєш", "заміняєте", "замінюєте",
                     "міняєте", "поміняти", "замінити", "заміна", "ставите", "встановлюєте")


def _wants_price(text: str) -> bool:
    """Client explicitly asks for a price/cost (not just describes a problem)."""
    t = (text or "").lower()
    return any(w in t for w in _PRICE_WORDS)


def _asks_capability(text: str) -> bool:
    """Client asks whether we do/service something (not a price)."""
    t = (text or "").lower()
    return any(w in t for w in _CAPABILITY_WORDS)


def _looks_substantive(text: str) -> bool:
    """Heuristic: is this a real service/info question (not a bare greeting)?"""
    t = (text or "").lower().strip()
    if len(t) < 3:
        return False
    if "?" in t or any(tr in t for tr in _SUBSTANTIVE_TRIGGERS):
        return True
    words = re.findall(r"[^\W\d_]+", t, re.UNICODE)
    if words and all(w in _GREETING_WORDS for w in words):
        return False
    return len(words) >= 2


def _query_tokens(*texts: str) -> list:
    tokens = []
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[\w\d]+", t.lower(), re.UNICODE):
            if len(w) >= 3 and w not in tokens:
                tokens.append(w)
    return tokens


async def _tool_list_categories(tenant_id: uuid.UUID, db: AsyncSession) -> str:
    """Cheap step 1: category names + service counts, without dumping prices."""
    from sqlalchemy import func
    res = await db.execute(
        select(ServiceCategory.title, func.count(ServicePrice.id))
        .join(ServicePrice, ServicePrice.category_id == ServiceCategory.id, isouter=True)
        .where(ServiceCategory.tenant_id == tenant_id)
        .group_by(ServiceCategory.title)
        .order_by(ServiceCategory.title)
    )
    rows = [(t, n) for t, n in res.all() if t]
    if not rows:
        return "Каталог порожній."
    return "Категорії послуг (оберіть і запитайте search_catalog по назві категорії або послуги):\n" + \
        "\n".join([f"- {t} ({n} послуг)" for t, n in rows])


# Everyday word -> technical synonyms used in the price list. Expands search
# tokens so "екран"/"розбитий екран" still finds "заміна матриці".
_CATALOG_SYNONYMS = {
    "екран": ["матриц", "дисплей"], "екрану": ["матриц", "дисплей"],
    "дисплей": ["матриц"], "скло": ["тачскрін", "матриц"],
    "батарея": ["акумулятор", "акб"], "батарею": ["акумулятор", "акб"],
    "акб": ["акумулятор"], "зарядка": ["роз'єм", "живлення"],
    "зарядки": ["роз'єм", "живлення"], "кнопка": ["шлейф"], "кнопки": ["шлейф"],
    # brand transliteration: client writes Cyrillic, price list often Latin
    "айфон": ["iphone"], "айфону": ["iphone"], "айфона": ["iphone"],
    "самсунг": ["samsung"], "ксіомі": ["xiaomi"], "сяомі": ["xiaomi"],
    "хуавей": ["huawei"], "ноут": ["ноутбук", "laptop"], "макбук": ["macbook"],
    "модуль": ["дисплейний модуль", "матриц", "дисплей"],
}


def _expand_tokens(tokens: list, synonyms: dict = None) -> list:
    syn_map = synonyms if synonyms is not None else _CATALOG_SYNONYMS
    out = list(tokens)
    for t in tokens:
        for syn in syn_map.get(t, []):
            if syn not in out:
                out.append(syn)
    return out


# Too-generic words that appear in almost every service name / category title.
# Searching by them returns junk from all categories ("Ремонт плати" everywhere).
_CATALOG_STOPWORDS = {
    "ремонт", "ремонту", "заміна", "заміну", "діагностика", "діагностики",
    "послуга", "послуги", "послуг", "техніки", "техніка", "пристрій", "пристрою",
    "відремонтувати", "полагодити", "поломка", "несправність", "майстер",
}


async def _tool_search_catalog(query: str, tenant_id: uuid.UUID, db: AsyncSession, synonyms: dict = None) -> str:
    """
    Targeted, paginated catalog search (keeps context small):
    1. exact-ish match by service name (ILIKE meaningful tokens + synonyms);
    2. else find the matching CATEGORY and return only its services;
    3. else return the category list so the model can drill down step by step.
    """
    syn = synonyms if synonyms is not None else _CATALOG_SYNONYMS
    raw = _query_tokens(query)
    tokens = _expand_tokens([t for t in raw if t not in _CATALOG_STOPWORDS], syn)
    if not tokens:
        # only generic words (e.g. "ремонт пилососа" -> "пилосос" kept) — if even
        # that is empty, show categories
        return await _tool_list_categories(tenant_id, db)

    # 1. by service name
    name_conds = [ServicePrice.name.ilike(f"%{tok}%") for tok in tokens]
    res = await db.execute(
        select(ServicePrice, ServiceCategory.title)
        .join(ServiceCategory, ServicePrice.category_id == ServiceCategory.id, isouter=True)
        .where(ServicePrice.tenant_id == tenant_id, or_(*name_conds))
        .limit(12)
    )
    prices = res.all()
    if prices:
        return "\n".join([f"- {cat or 'Послуги'}: {p.name} — {p.price}" for p, cat in prices])

    # 2. find ONE matching category, return ONLY its services
    cat_conds = [ServiceCategory.title.ilike(f"%{tok}%") for tok in tokens]
    res_c = await db.execute(
        select(ServiceCategory.id, ServiceCategory.title)
        .where(ServiceCategory.tenant_id == tenant_id, or_(*cat_conds)).limit(1)
    )
    cat_row = res_c.first()
    if cat_row:
        cat_id, cat_title = cat_row
        res = await db.execute(
            select(ServicePrice.name, ServicePrice.price)
            .where(ServicePrice.category_id == cat_id).limit(20)
        )
        rows = res.all()
        if rows:
            return f"Послуги категорії «{cat_title}»:\n" + "\n".join([f"- {n} — {p}" for n, p in rows])

    # 3. nothing -> category list for step-by-step drill-down
    return "Прямого збігу немає. " + await _tool_list_categories(tenant_id, db)


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


def _parse_csv_set(s, default):
    """Editable comma/newline list from the panel -> tuple of lowercase phrases."""
    if not s or not str(s).strip():
        return default
    items = [x.strip().lower() for x in re.split(r"[,;\n]", str(s)) if x.strip()]
    return tuple(items) if items else default


def _parse_synonyms_map(s, default):
    """Editable synonyms: 'екран=матриця,дисплей' per line -> {word:[syn,...]}."""
    if not s or not str(s).strip():
        return default
    out = {}
    for line in re.split(r"[;\n]", str(s)):
        if "=" in line:
            k, vs = line.split("=", 1)
            k = k.strip().lower()
            vals = [v.strip().lower() for v in vs.split(",") if v.strip()]
            if k and vals:
                out[k] = vals
    return out or default


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

    enabled_tools = list(meta.get("enabled_tools") or ALL_TOOLS)
    # Forward-compat: keep newer helper tools usable even if a tenant saved an
    # older enabled_tools list (list_categories pairs with the catalog).
    if "search_catalog" in enabled_tools and "list_categories" not in enabled_tools:
        enabled_tools.append("list_categories")

    # Catalog synonyms (panel-editable) — used to match the client's everyday
    # words to the price list. The MODEL decides routing now (via the prompt),
    # so the old phrase-trigger heuristics are gone.
    syn_map = _parse_synonyms_map(meta.get("catalog_synonyms"), _CATALOG_SYNONYMS)

    async def catalog(q):
        return await _tool_search_catalog(q, tenant_id, db, synonyms=syn_map)
    max_iter = 4
    try:
        max_iter = int(meta.get("agent_max_iterations", DEFAULT_MAX_ITERATIONS))
    except (ValueError, TypeError):
        pass

    tools_block = "\n".join([TOOL_DESCRIPTIONS[t] for t in enabled_tools if t in TOOL_DESCRIPTIONS])
    decision_rules = (meta.get("agent_decision_rules") or "").strip() or DEFAULT_DECISION_RULES
    router_protocol = (ROUTER_PROTOCOL
                       .replace("{tools_block}", tools_block)
                       .replace("{decision_rules}", decision_rules)
                       .replace("{max_iter}", str(max_iter)))

    # Tenant routing hints from the "Схема Логіки (Інтенти)" page: each enabled
    # KnowledgeType row becomes a hint line, so adding a step in the panel
    # teaches the agent which tool to use for which trigger phrases.
    _HANDLER_TO_TOOL = {
        "qa_handler": "search_catalog / search_knowledge",
        "web_search_handler": "web_research",
        "site_search": "open_url",
        "escalate": "escalate",
    }
    try:
        from app.models.tenant import KnowledgeType
        res_kt = await db.execute(
            select(KnowledgeType)
            .where(KnowledgeType.tenant_id == tenant_id, KnowledgeType.enabled == True)
            .order_by(KnowledgeType.priority)
        )
        hint_lines = []
        for kt in res_kt.scalars().all():
            tool_hint = _HANDLER_TO_TOOL.get(kt.handler)
            reasoning = (kt.meta.get("reasoning") if kt.meta else "") or ""
            # Keep an intent even without a known tool if it carries a reasoning
            # template (generalization rule the model should follow).
            if not tool_hint and not reasoning:
                continue
            if kt.handler == "site_search" and kt.meta and kt.meta.get("target_url"):
                tool_hint = f"open_url ({kt.meta['target_url']})"
            patterns = ""
            if kt.intent_patterns:
                patterns = " Trigger phrases: " + ", ".join(kt.intent_patterns[:6]) + "."
            line = f"- {kt.label or kt.code}:"
            if tool_hint:
                line += f" use {tool_hint}."
            line += patterns
            if reasoning:
                # Slot templates like "ви ремонтуєте {прилад}" tell the model to
                # extract the slot and reason about it before searching.
                line += f" How to reason: {reasoning}"
            hint_lines.append(line)
        if hint_lines:
            router_protocol += "\n[TENANT ROUTING HINTS]\n" + "\n".join(hint_lines)
    except Exception as e:
        logger.warning(f"Could not load routing hints: {e}")

    persona = settings.system_prompt if settings and settings.system_prompt else "You are a helpful assistant. Answer in Ukrainian."
    business_rules = settings.business_rules if settings and settings.business_rules else ""

    memory = dict(memory or {})
    gathered = []          # [(action, query, result)]
    actions_done = set()
    # Strict JSON for the router (cloud models). Disable per-tenant if needed.
    use_json_mode = bool(meta.get("router_json_mode", True))

    base_url = meta.get("llm_base_url")
    api_key = meta.get("llm_api_key")
    model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"

    from app.config import settings as _app_settings
    serper_key = (meta.get("serper_api_key") or "").strip() or (getattr(_app_settings, "SERPER_API_KEY", "") or None)
    fallback_sites = meta.get("fallback_sites", "")
    parts_sites = meta.get("parts_sites", "")

    async def _do_web_research(q: str, sites: str = None, fallback_open: bool = True) -> str:
        """Web research. If `sites` given, restrict to them first; optionally
        fall back to the open web."""
        sites = sites if sites is not None else fallback_sites
        result = ""
        if sites:
            site_list = [s.strip() for s in sites.split(",") if s.strip()]
            sites_q = " OR ".join([f"site:{s}" for s in site_list])
            result = await asyncio.to_thread(web_research, f"({sites_q}) {q}", 3, 4000, serper_key)
            if "No search results" in result or "could not extract" in result.lower():
                result = ""
        if not result and fallback_open:
            result = await asyncio.to_thread(web_research, q, 3, 4000, serper_key)
        # If search yielded nothing / was blocked — make it an explicit NO-DATA
        # instruction so the model does NOT invent links or prices.
        if (not result) or ("No search results" in result) or ("ПОШУК ЗАБЛОКОВАНО" in result) \
                or ("Search error" in result) or ("could not extract" in result.lower()):
            return ("[NO WEB DATA — search returned nothing. You MUST NOT invent any links, shops, "
                    "or prices. Tell the client honestly you couldn't find it right now and offer to "
                    "check / take the device in.]\n" + (result or ""))
        return result

    # External part-price logic/labelling (panel field). Default at module level.
    parts_instruction = (meta.get("parts_instruction") or "").strip() or DEFAULT_PARTS_INSTRUCTION

    async def _do_search_parts(q: str) -> str:
        """Market price of a part from external supplier sites first, then open
        web. Treatment/labelling comes from the editable parts_instruction."""
        res = await _do_web_research(q, sites=parts_sites)  # parts sites first, web fallback
        header = "[EXTERNAL PART PRICES — MARKET, NOT OURS. How to treat: " + parts_instruction + "]\n"
        if not res or "No search results" in res or "ПОШУК ЗАБЛОКОВАНО" in res:
            return header + (res or "ринкову ціну не знайдено.")
        return header + res

    def _is_empty(result: str) -> bool:
        """True if a tool returned no useful facts (only emptiness markers)."""
        if not result:
            return True
        low = result.lower()
        markers = ["нічого не знайдено", "каталог порожній", "no search results",
                   "could not extract", "не знайдено у базі", "не налаштована",
                   # catalog returned only the category list, not an actual price
                   "прямого збігу немає", "категорії послуг (оберіть"]
        return any(m in low for m in markers)

    # Chat memory = only the short durable facts the model itself saved
    # (memory_patch: device model, stage). No raw lookup dumps carried over — that
    # was the context bloat that caused hallucinations.
    def build_context_block() -> str:
        parts = []
        visible = {k: v for k, v in memory.items() if not k.startswith("_")}
        if visible:
            parts.append("[CHAT MEMORY]\n" + "\n".join([f"- {k}: {v}" for k, v in visible.items()]))
        if gathered:
            facts = []
            for action, query, result in gathered:
                facts.append(f"--- {action}('{query}') ---\n{result}")
            parts.append("[GATHERED FACTS]\n" + "\n".join(facts))
        return "\n\n".join(parts)

    escalated = False

    # Short business identity for the ROUTER stage (full persona/tone is only for
    # the final answer — at routing it just makes small models ignore the JSON).
    identity = persona.split("\n\n")[0][:600] if persona else ""

    for iteration in range(1, max_iter + 1):
        # ROUTER protocol FIRST so the model obeys the JSON format instead of the
        # persona's "talk to the client" tone.
        sys_prompt = router_protocol
        sys_prompt += "\n\n[WHO YOU ARE]\n" + identity
        if business_rules:
            sys_prompt += "\n\n[BUSINESS RULES]\n" + business_rules
        context_block = build_context_block()
        if context_block:
            sys_prompt += "\n\n" + context_block
        sys_prompt += "\n\nReturn ONLY the JSON decision now."

        messages = [{"role": "system", "content": sys_prompt}]
        recent = (history or [])[-6:]
        for h in recent:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        if not recent or recent[-1].get("content") != text or recent[-1].get("role") != "user":
            messages.append({"role": "user", "content": text})

        t0 = time.time()
        # Ask the provider for strict JSON (cloud models support response_format).
        # If the provider rejects json_mode, retry once without it.
        try:
            raw, usage = await chat(
                messages, model=model_name, temperature=0.1, max_tokens=400,
                base_url=base_url, api_key=api_key, return_usage=True,
                raise_error=True, json_mode=use_json_mode
            )
        except Exception as je:
            if use_json_mode:
                use_json_mode = False  # provider doesn't support it — stop trying
                logger.warning(f"json_mode unsupported, retrying plain: {je}")
                raw, usage = await chat(
                    messages, model=model_name, temperature=0.1, max_tokens=400,
                    base_url=base_url, api_key=api_key, return_usage=True, raise_error=True
                )
            else:
                raise
        try:
            decision = _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            # Model broke the JSON protocol. Do NOT give up here (that would skip
            # tool use entirely). Treat as "answer" so the GUARD below still
            # forces the needed lookups before the final reply.
            emit(f"AGENT ROUTER #{iteration}", "JSON помилка → GUARD", f"{e}\nRAW: {raw[:200]}", f"{time.time() - t0:.2f}s")
            decision = {"action": "answer", "query": "", "reason": "json_parse_failed", "memory_patch": {}}

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

        # The MODEL decides (per the decision rules in the prompt). The engine only
        # runs the tool and feeds the result back — no hardcoded forcing/branching.
        if action == "answer" or action not in enabled_tools:
            break

        # Block only an IDENTICAL repeat (same action+query) to avoid loops; a
        # different query is allowed (step-by-step search until something is found).
        action_key = f"{action}:{query.lower().strip()}"
        if action_key in actions_done:
            emit(f"AGENT TOOL #{iteration}", "Пропущено", f"'{action}' з тим самим запитом вже виконувався")
            break
        actions_done.add(action_key)

        # Execute tool. Each result is wrapped with a MINI-PROMPT (how to read it)
        # so the model knows what the data means and how to use it.
        t0 = time.time()
        if action == "list_categories":
            result = "[OUR SERVICE AREAS — what we do. If the client's item fits one, we handle it.]\n" + await _tool_list_categories(tenant_id, db)
        elif action == "search_catalog":
            raw = await catalog(query or text)
            if _is_empty(raw):
                result = "[OUR CATALOG: no exact match. It does NOT prove we don't do it — check the site/web or ask. Do not quote prices.]\n" + raw
            else:
                result = "[OUR PRICES (ours). Use ONE relevant price only if the client asked about price; otherwise just confirm we do it.]\n" + raw
        elif action == "search_knowledge":
            result = "[OUR KNOWLEDGE BASE (FAQ/conditions). Rephrase in your own words.]\n" + await _tool_search_knowledge(query or text, tenant_id, db, settings)
        elif action == "search_parts":
            result = await _do_search_parts(query or text)  # already labelled inside
        elif action == "web_research":
            result = await _do_web_research(query or text)  # already labelled inside
        elif action == "open_url":
            result = await asyncio.to_thread(fetch_and_parse_url, query) if query.startswith("http") else "open_url потребує повного URL у query."
        elif action == "get_business_info":
            result = "[OUR BUSINESS FACTS (address/hours/payment). Give only what was asked.]\n" + _tool_get_business_info(query, settings)
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
    if marketing:
        sys_prompt += "\n\n[MARKETING — apply ONLY if it fits the context naturally, never forced]\n" + marketing
    context_block = build_context_block()
    if context_block:
        sys_prompt += "\n\n" + context_block
        # Tenant-editable anti-hallucination rules (panel: "Правила оцінки контексту")
        eval_rules = meta.get("tpl_evaluation_rules")
        if eval_rules:
            sys_prompt += "\n\n" + eval_rules
    # Tenant-editable escalation guidance (panel: "Настанова ескалації") — what to
    # say when the needed fact was not found anywhere.
    escalation_prompt = settings.escalation_prompt if settings and settings.escalation_prompt else ""
    if escalation_prompt:
        sys_prompt += "\n\n[IF THE ANSWER IS MISSING FROM THE FACTS]\nUse this guidance in your own words: " + escalation_prompt
    # Tone of the final reply — editable per tenant (panel), default in code.
    answer_style = (meta.get("answer_style") or "").strip() or DEFAULT_ANSWER_STYLE
    sys_prompt += "\n\n" + answer_style

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
    answer = _clean_answer(answer)
    # Only memory_patch (short durable facts) persists between messages — no raw
    # lookup dumps. Keeps the next turn's context clean.
    memory.pop("_facts", None)
    emit("AGENT ANSWER", "OK", f"Кроків циклу: {len(gathered)}", f"{time.time() - t0:.2f}s")
    return answer, memory
