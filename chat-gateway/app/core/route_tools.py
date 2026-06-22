"""Source tools and small cleaners used by the active conversation pipeline."""

from __future__ import annotations

import json
import logging
import re
import uuid

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rag import search_knowledge
from app.models.knowledge import QaPair
from app.models.services import ServiceCategory, ServicePrice


logger = logging.getLogger(__name__)


_JUNK_PATTERNS = [
    r"(?im)^\s*we already gave final\.?\s*$",
    r"(?i)\bwe already gave final\.?",
    r"(?i)\bwe already answered\.?",
    r"(?im)^\s*MODE:.*$",
    r"(?im)^\s*\{.*\"action\".*\}\s*$",
    r"(?im)^\s*(reason|action|memory_patch|query)\s*[:=].*$",
    r"(?is)\bNeed to perform (?:a )?web search\.?\s*",
    r"(?is)\[Searching (?:the )?web[^\]]*\]\s*",
    r"(?i)\bSearch\.\.\.\s*",
    r"(?i)від\s*X\s*грн\s*до\s*Y\s*грн",
    r"(?i)від\s*X\s*до\s*Y(\s*грн)?",
    r"(?i)\bвід\s*[XY]\s*грн\b",
    r"(?i)\b[XY]\s*грн\s*(до|–|-)\s*[XY]\s*грн\b",
]


def _clean_answer(text: str, fallback: str = "") -> str:
    """Strip leaked internal artefacts from the client-facing reply."""
    if not text or str(text).strip().lower() in {"none", "null", "undefined", "nil"}:
        return fallback
    out = text
    self_note = re.search(r"(?i)\bwe already (?:answered|gave final)\.?", out)
    if self_note and out[:self_note.start()].strip():
        out = out[:self_note.start()]
    for pat in _JUNK_PATTERNS:
        out = re.sub(pat, "", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out or fallback


def _extract_json(text: str) -> dict:
    """Tolerant JSON extraction: handles ```json fences and stray prose."""
    if not text:
        raise ValueError("empty controller response")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in: {text[:200]}")
    return json.loads(match.group(0))


def _query_tokens(*texts: str) -> list:
    tokens = []
    for t in texts:
        if not t:
            continue
        for w in re.findall(r"[\w\d]+", t.lower(), re.UNICODE):
            if (len(w) >= 3 or (w.isdigit() and len(w) >= 2)) and w not in tokens:
                tokens.append(w)
    return tokens


_CATALOG_SYNONYMS = {
    "екран": ["матриц", "дисплей"], "екрану": ["матриц", "дисплей"],
    "дисплей": ["матриц"], "скло": ["тачскрін", "матриц"],
    "батарея": ["акумулятор", "акб"], "батарею": ["акумулятор", "акб"],
    "акб": ["акумулятор"], "зарядка": ["роз'єм", "живлення"],
    "зарядки": ["роз'єм", "живлення"], "кнопка": ["шлейф"], "кнопки": ["шлейф"],
    "айфон": ["iphone", "смартфон"], "айфону": ["iphone", "смартфон"], "айфона": ["iphone", "смартфон"],
    "iphone": ["смартфон"], "андроїд": ["смартфон"],
    "телефон": ["смартфон", "мобільний"], "телефона": ["смартфон", "мобільний"],
    "телефону": ["смартфон", "мобільний"],
    "самсунг": ["samsung"], "ксіомі": ["xiaomi"], "сяомі": ["xiaomi"],
    "хуавей": ["huawei"], "ноут": ["ноутбук", "laptop"], "макбук": ["macbook"],
    "модуль": ["дисплейний модуль", "матриц", "дисплей"],
    "босе": ["bose"], "боус": ["bose"], "маршал": ["marshall"],
    "мейджор": ["major"], "джбл": ["jbl"], "соні": ["sony"],
    "епл": ["apple"], "леново": ["lenovo"], "асус": ["asus"],
    "ейсер": ["acer"], "делл": ["dell"], "хп": ["hp"],
    "навушники": ["гарнітура", "headphones", "earbuds"],
    "колонка": ["акустика", "speaker"], "колонки": ["акустика", "speakers"],
    "павербанк": ["powerbank", "зовнішній акумулятор"],
    "зарядна": ["зарядна станція", "power station"],
    "ecoflow": ["зарядна станція", "інвертор"], "екофлоу": ["зарядна станція", "інвертор"],
    "пилосос": ["порохотяг"], "кавоварка": ["кавомашина", "кавовий апарат"],
    "гніздо": ["роз'єм", "порт"], "порт": ["роз'єм", "гніздо"],
    "тайпсі": ["type-c", "usb-c"], "typec": ["type-c", "usb-c"],
    "мікроюсб": ["micro-usb"], "залив": ["чистка після залиття", "корозія"],
    "вода": ["рідина", "залиття"], "води": ["рідина", "залиття"],
    "воду": ["рідина", "залиття"], "водою": ["рідина", "залиття"],
    "рідина": ["залиття", "волога"], "намок": ["рідина", "залиття", "волога"],
    "заряджається": ["заряджання", "зарядки", "роз'єм"],
    "заряджаються": ["заряджання", "зарядки", "роз'єм"],
    "протікає": ["протікання", "витік"], "тече": ["протікання", "витік"],
    "підсвітка": ["підсвітки", "led-підсвітки"],
    "гріється": ["перегрів", "чистка", "термоінтерфейс"],
    "хрипить": ["динамік", "акустика"], "звук": ["динамік", "мікрофон", "аудіо"],
}


def _expand_tokens(tokens: list, synonyms: dict | None = None) -> list:
    syn_map = synonyms if synonyms is not None else _CATALOG_SYNONYMS
    out = list(tokens)
    for t in tokens:
        for syn in syn_map.get(t, []):
            if syn not in out:
                out.append(syn)
    return out


_CATALOG_STOPWORDS = {
    "ремонт", "ремонту", "заміна", "заміну", "діагностика", "діагностики",
    "послуга", "послуги", "послуг", "техніки", "техніка", "пристрій", "пристрою",
    "відремонтувати", "полагодити", "поломка", "несправність", "майстер",
}


async def _tool_list_categories(tenant_id: uuid.UUID, db: AsyncSession) -> str:
    """Category headings plus short descriptions, without prices or item rows."""
    res = await db.execute(
        select(ServiceCategory.title, ServiceCategory.description)
        .where(ServiceCategory.tenant_id == tenant_id, ServiceCategory.enabled == True)
        .order_by(ServiceCategory.title)
    )
    rows = []
    for title, description in res.all():
        title = (title or "").strip()
        if not title:
            continue
        desc = " ".join((description or "").strip().split())[:160]
        rows.append(f"{title}: {desc}" if desc else title)
    if not rows:
        return "Каталог порожній."
    return "Заголовки категорій:\n" + "\n".join([f"- {row}" for row in rows])


async def _tool_search_catalog(
    query: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    synonyms: dict | None = None,
    requested_fact: str = "",
) -> str:
    """Candidate retrieval for route-side semantic validation."""
    syn = synonyms if synonyms is not None else _CATALOG_SYNONYMS
    raw = _query_tokens(query)
    tokens = _expand_tokens([t for t in raw if t not in _CATALOG_STOPWORDS], syn)
    if not tokens:
        return await _tool_list_categories(tenant_id, db)

    def search_form(token: str) -> str:
        return token[:max(4, len(token) - 2)] if len(token) >= 6 else token

    search_tokens = []
    for token in tokens:
        form = search_form(token)
        if form and form not in search_tokens:
            search_tokens.append(form)

    name_conds = [ServicePrice.name.ilike(f"%{tok}%") for tok in search_tokens]
    name_conds += [ServicePrice.description.ilike(f"%{tok}%") for tok in search_tokens]
    cat_conds = [ServiceCategory.title.ilike(f"%{tok}%") for tok in search_tokens]
    cat_conds += [ServiceCategory.description.ilike(f"%{tok}%") for tok in search_tokens]
    res = await db.execute(
        select(ServicePrice, ServiceCategory)
        .join(ServiceCategory, ServicePrice.category_id == ServiceCategory.id)
        .where(
            ServicePrice.tenant_id == tenant_id,
            ServiceCategory.enabled == True,
            or_(*(name_conds + cat_conds)),
        )
        .limit(300)
    )
    candidates = res.all()
    if candidates:
        fact = (requested_fact or "").strip().lower()
        scope_only = fact in {"availability", "scope", "scope_check", "наличие", "наявність"}
        original_forms = [(tok, search_form(tok)) for tok in raw if tok not in _CATALOG_STOPWORDS]
        expanded_forms = [search_form(tok) for tok in tokens if tok not in raw]

        def score(row) -> tuple:
            price, category = row
            name = (price.name or "").lower()
            category_text = (category.title or "").lower()
            category_meta = category.meta or {}
            item_meta = getattr(price, "meta", None) or {}
            description = " ".join([
                category.description or "",
                str(category_meta.get("detailed_description") or ""),
                getattr(price, "description", "") or "",
                "" if scope_only else str(item_meta.get("characteristics") or ""),
                "" if scope_only else str(item_meta.get("work_scope") or item_meta.get("composition") or ""),
            ]).lower()
            phrase = f"{category_text} {name} {description}"
            original_hits = sum(1 for _, form in original_forms if form in phrase)
            name_hits = sum(1 for _, form in original_forms if form in name)
            category_hits = sum(1 for _, form in original_forms if form in category_text)
            synonym_hits = sum(1 for form in expanded_forms if form in phrase)
            value = original_hits * 10 + name_hits * 12 + category_hits * 6 + synonym_hits
            return value, name_hits, category_hits

        # Category filter — SAFE version. Collapse to one category ONLY when the
        # whole top is already that single category (a clean hit, e.g. кавоварка).
        # On a MIXED top, keep the mixed top-6 and let the validator pick — never let
        # a noisy top-1 from the wrong category crush the correct rows (e.g. an
        # iPhone-display query where "про" matched "проектор" pushed TVs to the top).
        ranked_all = sorted(candidates, key=score, reverse=True)
        top6 = ranked_all[:6]
        cat_ids = {r[1].id for r in top6}
        if len(cat_ids) == 1 and top6:
            ranked = [r for r in ranked_all if r[1].id == top6[0][1].id][:8]
        else:
            ranked = top6
        lines = []
        for price, category in ranked:
            category_meta = category.meta or {}
            item_meta = getattr(price, "meta", None) or {}
            bits = [f"- CATEGORY: {category.title or 'Каталог'}"]
            if category.description:
                bits.append(f"category_short: {category.description}")
            if not scope_only and category_meta.get("detailed_description"):
                bits.append(f"category_deep: {category_meta.get('detailed_description')}")
            bits.append(f"ITEM: {price.name}")
            if not scope_only and item_meta.get("item_type"):
                bits.append(f"item_type: {item_meta.get('item_type')}")
            if not scope_only:
                bits.append(f"price_or_condition: {price.price}")
            if not scope_only and item_meta.get("availability"):
                bits.append(f"availability_or_status: {item_meta.get('availability')}")
            if not scope_only and item_meta.get("characteristics"):
                bits.append(f"characteristics: {item_meta.get('characteristics')}")
            work_scope = item_meta.get("work_scope") or item_meta.get("composition")
            if not scope_only and work_scope:
                bits.append(f"work_scope_or_contents: {work_scope}")
            if not scope_only and getattr(price, "description", None):
                bits.append(f"item_note_for_model: {price.description}")
            lines.append(" | ".join(bits))
        return "\n".join(lines)

    return "У внутрішньому каталозі немає рядка або категорії, що збігається із запитом."


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
        qa_conditions += [cast(QaPair.question_variants, String).ilike(f"%{tok}%") for tok in tokens]
        qa_conditions += [QaPair.category.ilike(f"%{tok}%") for tok in tokens]
        res_qa = await db.execute(
            select(QaPair)
            .where(QaPair.tenant_id == tenant_id, QaPair.enabled == True, or_(*qa_conditions))
            .limit(48)
        )
        qa_rows = res_qa.scalars().all()

        def qa_score(qa):
            question = (qa.question or "").lower()
            variants = " ".join(str(v) for v in (qa.question_variants or [])).lower()
            answer = (qa.answer or "").lower()
            category = (qa.category or "").lower()
            return sum(
                12 * (tok in question) +
                10 * (tok in variants) +
                3 * (tok in answer) +
                2 * (tok in category)
                for tok in tokens
            )

        for qa in sorted(qa_rows, key=qa_score, reverse=True)[:6]:
            parts.append(f"Q: {qa.question}\nA: {qa.answer}")
    try:
        embed_model = (settings.meta or {}).get("embed_model") if settings and settings.meta else None
        rag_docs = await search_knowledge(query, str(tenant_id), top_k=top_k, threshold=threshold, embed_model=embed_model)
        for doc in rag_docs:
            parts.append(f"[Документ]: {doc}")
    except Exception as e:
        logger.error(f"RAG error in route tool: {e}")
    return "\n---\n".join(parts) if parts else "Нічого не знайдено у базі знань."


def _tool_get_business_info(query: str, settings) -> str:
    info = settings.meta.get("business_info") if settings and settings.meta else None
    if not info:
        return "Бізнес-інформація не налаштована."
    if isinstance(info, dict):
        return "\n".join([f"{k}: {v}" for k, v in info.items() if v])
    return str(info)


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
