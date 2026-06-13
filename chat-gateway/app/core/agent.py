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

ROUTER_PROTOCOL = """MODE: ROUTER_DECISION
You are deciding the NEXT STEP for answering the client. You are NOT talking to the client now.
Return ONLY valid compact JSON, no markdown, no explanations:
{"action": "<action>", "query": "<query or empty>", "reason": "<short>", "memory_patch": {}}

Allowed actions:
{tools_block}
"answer": you already have enough verified facts (or none are needed — greetings, small talk, tone-only replies). This ends the loop.

Decision rules:
- Greetings, thanks, small talk, emotions → "answer" immediately. NEVER search for greetings.
- ANY question about whether we do / repair / sell / service something, about prices, services, availability, conditions, hours, address — you MUST gather facts BEFORE answering. Do NOT answer such questions from your own memory. If [GATHERED FACTS] is still empty, you are NOT allowed to "answer" a substantive question yet — pick a tool.
- "чи ремонтуєте X / робите ви X / маєте X / скільки коштує X" → search_catalog (it also returns the list of our categories so you can confirm or deny). Then search_knowledge if still unclear.
- Follow the chronology of the chat. Previous client requests stay active context until the topic clearly changes.
- Prices/availability/services of OUR business → search_catalog first. The internet is NOT our price source.
- Price of a SPARE PART for a model not in our catalog → search_parts (it queries external supplier sites and returns a THIRD-PARTY market price, not ours).
- Technical specs, compatibility, repair data missing from internal sources → web_research.
- Search QUERIES: try BOTH languages depending on the source — Ukrainian wording for local UA shops/parts and prices ("модуль iPhone 15 ціна купити"), English for worldwide specs/datasheets ("iPhone 15 display module replacement"). Do not limit yourself to one language; reformulate if the first query finds nothing.
- If a concrete model/detail is missing and needed → "answer" (you will ask the client for it).
- Do not repeat an action that already returned results this turn. If a lookup is listed under [ALREADY CHECKED THIS CHAT], reuse that result instead of searching again. Maximum {max_iter} steps, then you must "answer".
- "memory_patch": durable facts about THIS client chat worth remembering (device model, chosen option, stage). Keys/values short strings. Empty object if nothing new.

Format examples ONLY (the words/devices here are placeholders — NEVER reuse them
in your answer; always use the CLIENT'S actual words and device):
Client: <greeting> -> {"action":"answer","query":"","reason":"greeting","memory_patch":{}}
Client: <do you service X?> -> {"action":"search_catalog","query":"<X>","reason":"check service","memory_patch":{}}
Client: <price of service for device Y> -> {"action":"search_catalog","query":"<service Y>","reason":"price lookup","memory_patch":{}}
Client: <working hours / address?> -> {"action":"get_business_info","query":"hours","reason":"business fact","memory_patch":{}}
Answer ONLY about the device the CLIENT mentioned. Do not introduce a different device.
"""

# Default final-answer style. Editable per tenant via meta.answer_style
# (Налаштування → «Стиль відповіді»). This is TONE, not engine mechanics.
DEFAULT_ANSWER_STYLE = """--- НАПИШИ ВІДПОВІДЬ КЛІЄНТУ ---
Тепер ти просто говориш із клієнтом — живою, теплою, розмовною українською, у СВОЄМУ стилі (як описано вище): коротко, по-людськи, можна з легким гумором чи підколом, де доречно. НЕ як робот, не шаблонно, не канцелярит. Це жива людина по той бік, не форма.
Прості орієнтири (не зачитуй — просто тримай у голові):
- Ціни/факти бери ТІЛЬКИ зі зібраного вище. Нема — не вигадуй: скажи чесно, спитай модель або «гляну на місці».
- Ринкову ціну деталі подавай як «глянув у постачальників, приблизно стільки» (+ лінк, якщо є), окремо від нашої роботи.
- НЕ сип цінами/прайсом, якщо клієнт прямо не спитав про ціну. На «зламалось / чи робите» — досить «так, робимо, а що саме не так?». Ціни називай ЛИШЕ коли про них питають.
- Дані з бази/каталогу ПЕРЕФРАЗОВУЙ своїми словами під контекст — не вивалюй прайс списком і не копіюй дослівно.
- Тільки саме повідомлення клієнту. Без англійської, без службових позначок, без назв внутрішніх категорій, без заглушок «X–Y грн»."""


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


def _expand_tokens(tokens: list) -> list:
    out = list(tokens)
    for t in tokens:
        for syn in _CATALOG_SYNONYMS.get(t, []):
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


async def _tool_search_catalog(query: str, tenant_id: uuid.UUID, db: AsyncSession) -> str:
    """
    Targeted, paginated catalog search (keeps context small):
    1. exact-ish match by service name (ILIKE meaningful tokens + synonyms);
    2. else find the matching CATEGORY and return only its services;
    3. else return the category list so the model can drill down step by step.
    """
    raw = _query_tokens(query)
    tokens = _expand_tokens([t for t in raw if t not in _CATALOG_STOPWORDS])
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
    max_iter = 4
    try:
        max_iter = int(meta.get("agent_max_iterations", DEFAULT_MAX_ITERATIONS))
    except (ValueError, TypeError):
        pass

    tools_block = "\n".join([TOOL_DESCRIPTIONS[t] for t in enabled_tools if t in TOOL_DESCRIPTIONS])
    router_protocol = ROUTER_PROTOCOL.replace("{tools_block}", tools_block).replace("{max_iter}", str(max_iter))

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
    forced_lookup_done = False
    # Strict JSON for the router (cloud models). Disable per-tenant if needed.
    use_json_mode = bool(meta.get("router_json_mode", True))

    base_url = meta.get("llm_base_url")
    api_key = meta.get("llm_api_key")
    model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"

    serper_key = meta.get("serper_api_key") or None
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
        return result

    # Editable logic/labelling for external part prices (panel field). The CODE
    # only does the mechanics (parts sites first, then open web); HOW to treat
    # and present the result is this tenant text, not hardcoded.
    DEFAULT_PARTS_INSTRUCTION = (
        "Коли деталі немає в нашому прайсі: спершу шукай ринкову ціну на Сайтах запчастин (парсинг), "
        "якщо там нема — гугли. ЯК ПОДАВАТИ КЛІЄНТУ: якщо знайшов ціни — скажи природно «я глянув у "
        "постачальників, ціни приблизно такі: ...» і дай 1-2 посилання (URL) з даних, якщо вони є. "
        "Додай нашу роботу з каталогу. Ціну деталі подавай як СТОРОННЮ ринкову (купується окремо), "
        "точну назве майстер після огляду. ЯКЩО ЦІНИ НЕ ЗНАЙШОВ або пошук порожній — НЕ вигадуй цифри: "
        "скажи «точної ціни зараз не знайду, але як привезете прилад — на місці підберемо»."
    )
    parts_instruction = (meta.get("parts_instruction") or "").strip() or DEFAULT_PARTS_INSTRUCTION

    async def _do_search_parts(q: str) -> str:
        """Market price of a part from external supplier sites first, then open
        web. Treatment/labelling comes from the editable parts_instruction."""
        res = await _do_web_research(q, sites=parts_sites)  # parts sites first, web fallback
        header = "[ЗОВНІШНІ ЦІНИ ДЕТАЛЕЙ — РИНКОВІ, НЕ НАШІ. Інструкція трактування: " + parts_instruction + "]\n"
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

    # Persistent per-chat lookup memory (survives between messages): things the
    # agent already searched this conversation, so it doesn't repeat them and can
    # fall back to them. Service keys start with "_" and are hidden from display.
    chat_facts = list(memory.get("_facts", []))  # [{tool, query, summary}]

    def build_context_block() -> str:
        parts = []
        visible = {k: v for k, v in memory.items() if not k.startswith("_")}
        if visible:
            parts.append("[CHAT MEMORY]\n" + "\n".join([f"- {k}: {v}" for k, v in visible.items()]))
        if chat_facts:
            lines = [f"- {f['tool']}('{f['query']}') → {f['summary']}" for f in chat_facts]
            parts.append("[ALREADY CHECKED THIS CHAT — do NOT repeat these lookups, reuse the result]\n" + "\n".join(lines))
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

        if action == "answer" or action not in enabled_tools:
            # Safety net for small local models: if it wants to answer a
            # substantive question without having gathered ANY facts, force a
            # catalog + knowledge sweep first, then let it decide again with
            # facts in hand. Prevents answering services/prices from memory.
            # Business-facts questions (hours, address, payment...) need
            # get_business_info, not the catalog/web sweep.
            if (action == "answer" and not forced_lookup_done
                    and _looks_business_info(text) and "get_business_info" in enabled_tools):
                forced_lookup_done = True
                r = _tool_get_business_info(text, settings)
                gathered.append(("get_business_info", text, r))
                actions_done.add("get_business_info")
                emit(f"AGENT GUARD #{iteration}", "get_business_info (forced)", str(r)[:800])
                continue

            # Specific model + part (e.g. «айфон 17 екран», «модуль на 15 про») →
            # ALWAYS go to the market, the model won't do it on its own. The
            # catalog has no exact price for a specific new model.
            if (action == "answer" and not forced_lookup_done
                    and _looks_specific_part_query(text, history)
                    and (("search_parts" in enabled_tools and parts_sites) or "web_research" in enabled_tools)):
                forced_lookup_done = True
                # quick catalog peek for our labour, then market price
                if "search_catalog" in enabled_tools:
                    rc = await _tool_search_catalog(text, tenant_id, db)
                    gathered.append(("search_catalog", text, rc))
                    actions_done.add("search_catalog")
                    emit(f"AGENT TOOL #{iteration}", "search_catalog (forced)", str(rc)[:800])
                if parts_sites and "search_parts" in enabled_tools:
                    rp = await _do_search_parts(text)
                    gathered.append(("search_parts", text, rp))
                    actions_done.add("search_parts")
                    emit(f"AGENT TOOL #{iteration}", "search_parts (forced)", str(rp)[:800])
                elif "web_research" in enabled_tools:
                    rw = await _do_web_research(text)
                    gathered.append(("web_research", text, rw))
                    actions_done.add("web_research")
                    emit(f"AGENT TOOL #{iteration}", "web_research (forced)", str(rw)[:800])
                continue

            # Capability question ("чи ремонтуєте X?", no price) → a single light
            # catalog check is enough to confirm we do it. NO web, NO knowledge,
            # no waterfall. Don't repeat if the model already searched.
            already_catalog = any(a == "search_catalog" for a, _, _ in gathered)
            if (action == "answer" and not forced_lookup_done
                    and _asks_capability(text) and not _wants_price(text)
                    and "search_catalog" in enabled_tools):
                forced_lookup_done = True
                if not already_catalog:
                    r = await _tool_search_catalog(text, tenant_id, db)
                    # Capability question — we only need YES/NO that we do it, NOT
                    # the prices. Inject a price-free verdict so the model can't
                    # dump the price list when nobody asked.
                    available = not _is_empty(r) or "категорії послуг" in r.lower()
                    verdict = ("[ДОСТУПНІСТЬ: так, така техніка/послуга у нас Є. Відповідай коротко «так, робимо» "
                               "і спитай ЩО САМЕ не працює / модель. НЕ називай жодних цін — клієнт про них не питав.]"
                               if available else
                               "[ДОСТУПНІСТЬ: прямого збігу в каталозі нема. Перевір сайт/інтернет або чесно скажи, "
                               "що уточниш. Без цін.]")
                    gathered.append(("availability_check", text, verdict))
                    actions_done.add("search_catalog")
                    emit(f"AGENT TOOL #{iteration}", "availability_check (forced, capability)", verdict)
                continue

            # Price question → catalog (+ knowledge), and the market only if the
            # catalog has no real price. Reuse the model's own search if it did one.
            if (action == "answer" and not forced_lookup_done
                    and _wants_price(text)
                    and ("search_catalog" in enabled_tools or "web_research" in enabled_tools)):
                forced_lookup_done = True
                emit(f"AGENT GUARD #{iteration}", "Примусовий пошук", "Запит ціни — каталог → (інтернет якщо нема)")
                catalog_hit = any(not _is_empty(r) for a, _, r in gathered if a == "search_catalog")
                if "search_catalog" in enabled_tools and not already_catalog:
                    r = await _tool_search_catalog(text, tenant_id, db)
                    gathered.append(("search_catalog", text, r))
                    actions_done.add("search_catalog")
                    catalog_hit = not _is_empty(r)
                    emit(f"AGENT TOOL #{iteration}", "search_catalog (forced)", str(r)[:600])
                if not catalog_hit:
                    if parts_sites and "search_parts" in enabled_tools:
                        r = await _do_search_parts(text)
                        gathered.append(("search_parts", text, r))
                        actions_done.add("search_parts")
                        emit(f"AGENT TOOL #{iteration}", "search_parts (forced)", str(r)[:600])
                    elif "web_research" in enabled_tools:
                        r = await _do_web_research(text)
                        gathered.append(("web_research", text, r))
                        actions_done.add("web_research")
                        emit(f"AGENT TOOL #{iteration}", "web_research (forced)", str(r)[:600])
                continue
            break

        # Allow repeating catalog/web/open_url with a DIFFERENT query (step-by-step
        # drill-down). Block only an identical repeat to avoid loops.
        repeatable = {"search_catalog", "search_knowledge", "web_research", "open_url"}
        action_key = f"{action}:{query.lower().strip()}"
        if action in actions_done and action not in repeatable:
            emit(f"AGENT TOOL #{iteration}", "Пропущено", f"'{action}' вже виконувався цього ходу")
            break
        if action_key in actions_done:
            emit(f"AGENT TOOL #{iteration}", "Пропущено", f"'{action}' з тим самим запитом вже виконувався")
            break
        actions_done.add(action)
        actions_done.add(action_key)

        # Execute tool
        t0 = time.time()
        if action == "list_categories":
            result = await _tool_list_categories(tenant_id, db)
        elif action == "search_catalog":
            result = await _tool_search_catalog(query or text, tenant_id, db)
        elif action == "search_knowledge":
            result = await _tool_search_knowledge(query or text, tenant_id, db, settings)
        elif action == "search_parts":
            result = await _do_search_parts(query or text)
        elif action == "web_research":
            result = await _do_web_research(query or text)
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
    # Deterministic price gate: if the client did NOT ask a price in THIS message,
    # forbid quoting any numbers — just confirm and ask what's wrong.
    if not _wants_price(text):
        sys_prompt += ("\n\n[ВАЖЛИВО: клієнт НЕ питав ціну в цьому повідомленні. НЕ називай жодних сум, "
                       "діапазонів чи прайсу, навіть якщо вони є у фактах. Просто підтверди по-людськи "
                       "(«так, робимо») і спитай, що саме не працює / яка модель.]")
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

    # Persist this turn's lookups into per-chat memory so the next message can
    # reuse them and the agent won't repeat the same searches.
    for action, query, result in gathered:
        if action in ("escalate",) or not result:
            continue
        summary = " ".join(str(result).split())[:300]
        chat_facts.append({"tool": action, "query": (query or text)[:80], "summary": summary})
    if chat_facts:
        # keep the most recent unique-ish lookups, cap size to bound tokens
        seen = set()
        deduped = []
        for f in reversed(chat_facts):
            key = f"{f['tool']}:{f['query'].lower()}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(f)
        memory["_facts"] = list(reversed(deduped[:12]))

    emit("AGENT ANSWER", "OK", f"Кроків циклу: {len(gathered)}, пам'ять чату: {len(memory.get('_facts', []))} знахідок", f"{time.time() - t0:.2f}s")
    return answer, memory
