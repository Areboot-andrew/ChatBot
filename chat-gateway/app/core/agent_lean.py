"""
Lean isolated-call agent (engine = "lean").

Owner's design — stop dragging one fat context through every model call. Each
stage is its OWN isolated call with only its own small prompt and only the data
it needs. Everything (routes, their prompts, the knowledge bases, the persona,
search) already exists in the DB — this module only fixes the LOGIC and splits
the memory:

  1. DECIDE  — persona + a COMPACT map built from the tenant's own routes
               (label + trigger phrases + code) + chat + already-cleaned facts.
               One job: pick a route or answer. Small -> reliable JSON.
  2. QUERY   — isolated call using ONLY that route's own query_prompt: turn the
               client's need into the source query. (Each route searches with
               its own prompt, its own little memory.)
  3. tool    — engine runs the real DB/web fetch for that route's tool.
  4. CLEAN   — isolated STATELESS call using ONLY that route's source_description
               + result_validation_prompt: raw source text -> clean facts.
  5. ANSWER  — persona + chat + the cleaned facts -> the client reply.

Only cleaned facts move forward; raw bases never enter the main context.
"""
import logging

from sqlalchemy import select

from app.core.llm import chat
from app.core.agent import (
    _tool_list_categories,
    _tool_search_catalog,
    _tool_search_knowledge,
    _tool_get_business_info,
    _extract_json,
    _clean_answer,
    _parse_synonyms_map,
    _CATALOG_SYNONYMS,
)

logger = logging.getLogger(__name__)

_EMPTY_MARKERS = (
    "нічого не знайдено", "каталог порожній", "no search results", "could not extract",
    "не знайдено у базі", "не налаштована", "прямого збігу немає", "no web data",
)


def _is_empty(result: str) -> bool:
    if not result or not result.strip():
        return True
    low = result.lower()
    return any(m in low for m in _EMPTY_MARKERS)


def _recent(history: list, text: str, n: int = 8) -> list:
    msgs = []
    for h in (history or [])[-n:]:
        msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
    if not history or history[-1].get("content") != text or history[-1].get("role") != "user":
        msgs.append({"role": "user", "content": text})
    return msgs


async def _load_routes(tenant_id, db):
    """Tenant routes from Схема Логіки — the single source of truth for which
    bases exist and how to query/validate each."""
    from app.models.tenant import KnowledgeType
    res = await db.execute(
        select(KnowledgeType).where(
            KnowledgeType.tenant_id == tenant_id, KnowledgeType.enabled == True
        ).order_by(KnowledgeType.priority)
    )
    routes = {}
    for kt in res.scalars().all():
        m = dict(kt.meta or {})
        routes[str(kt.code)] = {
            "code": str(kt.code),
            "label": kt.label or kt.code,
            "handler": kt.handler,
            "tool_name": (m.get("tool_name") or "").strip(),
            "triggers": list(kt.intent_patterns or []),
            "query_prompt": (m.get("query_prompt") or "").strip(),
            "source_description": (m.get("source_description") or "").strip(),
            "result_validation_prompt": (m.get("result_validation_prompt") or "").strip(),
            "target_url": (m.get("target_url") or "").strip(),
        }
    return routes


def _source_map(routes: dict) -> str:
    """Compact router map from the tenant's own routes — a few lines, no essays."""
    lines = ["Decide the NEXT step. Reply with ONE compact JSON line only:",
             '{"route":"<route code or empty>","action":"<route code | answer>"}',
             "", "Available routes (pick the one whose data answers the client; else answer):"]
    for r in routes.values():
        trig = ", ".join(r["triggers"][:6])
        lines.append(f"- {r['code']} — {r['label']}." + (f" Triggers: {trig}." if trig else ""))
    lines.append("- answer — reply now: greeting, small talk, off-topic, or you already have the facts.")
    return "\n".join(lines)


_TOOL_BY_HANDLER = {"qa_handler": "search_catalog", "web_search_handler": "web_research", "site_search": "open_url"}


async def _run_tool(route, query, text, tenant_id, db, settings, syn_map, serper_key):
    tool = route.get("tool_name") or _TOOL_BY_HANDLER.get(route.get("handler"), "")
    q = query or text
    if tool == "search_catalog":
        return await _tool_search_catalog(q, tenant_id, db, synonyms=syn_map), tool
    if tool == "list_categories":
        return await _tool_list_categories(tenant_id, db), tool
    if tool == "search_knowledge":
        return await _tool_search_knowledge(q, tenant_id, db, settings), tool
    if tool == "get_business_info":
        return _tool_get_business_info(query, settings), tool
    if tool in ("web_research", "search_parts"):
        import asyncio
        from app.core.tools import web_research
        return await asyncio.to_thread(web_research, q, 3, 3000, serper_key), tool
    if tool == "open_url":
        import asyncio
        from urllib.parse import quote
        from app.core.tools import fetch_and_parse_url
        url = route.get("target_url", "")
        if "{query}" in url:
            url = url.replace("{query}", quote(q))
        return (await asyncio.to_thread(fetch_and_parse_url, url) if url.startswith("http") else ""), tool
    return "", tool


async def run_agent_lean(text, history, tenant_id, db, settings, trace=None, memory=None):
    emit = trace or (lambda *a, **k: None)
    memory = dict(memory or {})
    meta = (settings.meta or {}) if settings else {}

    persona = (settings.system_prompt if settings and settings.system_prompt
               else "Ти майстер сервісного центру. Відповідай українською, коротко й по-людськи.")
    business_rules = settings.business_rules if settings and settings.business_rules else ""
    if business_rules:
        persona += "\n\n[BUSINESS RULES]\n" + business_rules

    base_url = meta.get("llm_base_url")
    api_key = meta.get("llm_api_key")
    model = settings.llm_model if settings and settings.llm_model else "gemma-4"
    syn_map = _parse_synonyms_map(meta.get("catalog_synonyms"), _CATALOG_SYNONYMS)
    serper_key = meta.get("serper_api_key")
    try:
        max_iter = min(3, max(1, int(meta.get("agent_max_iterations", 3))))
    except (ValueError, TypeError):
        max_iter = 3

    routes = await _load_routes(tenant_id, db)
    source_map = _source_map(routes)

    facts = []
    done = set()

    for step in range(1, max_iter + 1):
        # 1) DECIDE — ONLY the route map + chat + cleaned facts. No persona, no
        # behaviour/tone/conduct rules — routing doesn't need them, and dragging
        # them is exactly what bloated the context. Tone lives only in ANSWER.
        sys = "[ROUTER MODE — not talking to the client, just pick the next step]\n" + source_map
        if facts:
            sys += "\n\n[FACTS YOU ALREADY HAVE]\n" + "\n".join(facts)
        dmsgs = [{"role": "system", "content": sys}] + _recent(history, text)
        raw = await _safe_chat(dmsgs, model, base_url, api_key, 0.1, 120, retry=True)
        emit(f"DECIDE #{step}", "Сире рішення", str(raw))
        try:
            decision = _extract_json(raw)
        except Exception:
            decision = {"action": "answer"}
        pick = str(decision.get("route") or decision.get("action") or "answer").strip()
        if pick in ("answer", "") or pick not in routes:
            break
        if pick in done:
            break
        done.add(pick)
        route = routes[pick]

        # 2) QUERY — isolated call with ONLY this route's own query_prompt
        query = await _build_query(route, text, history, model, base_url, api_key, emit, step)

        # 3) tool — raw fetch
        raw_result, tool = await _run_tool(route, query, text, tenant_id, db, settings, syn_map, serper_key)
        emit(f"TOOL #{step}", f"{route['code']} → {tool}", str(raw_result)[:1500])

        # 4) CLEAN — isolated stateless call with this route's own clean prompt
        cleaned = await _clean_source(route, query or text, raw_result, model, base_url, api_key, emit, step)
        if cleaned:
            facts.append(f"[{route['label']}] {cleaned}")

    # 5) ANSWER — persona + chat + cleaned facts only
    ans_sys = persona
    if facts:
        ans_sys += ("\n\n[VERIFIED FACTS — answer only from these and the client's own words. "
                    "Never invent an address, price, phone or schedule not listed here.]\n" + "\n".join(facts))
    amsgs = [{"role": "system", "content": ans_sys}] + _recent(history, text)
    try:
        temp = float(settings.temperature) if settings and settings.temperature else 0.3
    except (ValueError, TypeError):
        temp = 0.3
    answer = await _safe_chat(amsgs, model, base_url, api_key, temp, 700, retry=False)
    if not answer:
        answer = settings.fallback_text if settings and settings.fallback_text else "Технічна заминка, спробуйте ще раз."
    answer = _clean_answer(answer, fallback=(settings.fallback_text if settings else "") or "")
    emit("ANSWER", "OK", f"Фактів: {len(facts)}")
    return answer, memory


async def _safe_chat(messages, model, base_url, api_key, temperature, max_tokens, retry=False):
    try:
        out = await chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                         base_url=base_url, api_key=api_key, raise_error=True)
    except Exception as e:
        logger.warning(f"lean chat failed: {e}")
        return ""
    if retry and not str(out or "").strip():
        try:
            out = await chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                             base_url=base_url, api_key=api_key, raise_error=True)
        except Exception:
            out = ""
    return out or ""


async def _build_query(route, text, history, model, base_url, api_key, emit, step):
    """Isolated query construction using ONLY this route's query_prompt."""
    qp = route.get("query_prompt")
    if not qp:
        return text
    sys = ("You build a short search query for ONE source. Output ONLY the query, no quotes, no JSON.\n\n"
           "How to build it: " + qp)
    msgs = [{"role": "system", "content": sys}] + _recent(history, text, n=4)
    q = await _safe_chat(msgs, model, base_url, api_key, 0.1, 40, retry=True)
    q = (q or "").strip().strip('"').splitlines()[0] if q else ""
    emit(f"QUERY #{step}", route["code"], q or text)
    return q or text


async def _clean_source(route, need, raw, model, base_url, api_key, emit, step):
    """Isolated stateless cleaner using ONLY this route's clean prompts."""
    if _is_empty(raw):
        emit(f"CLEAN #{step}", "Порожньо", "Джерело без корисних даних")
        return ""
    rules = route.get("result_validation_prompt") or route.get("source_description") or ""
    user = (
        f"Клієнту потрібно: {need}\n"
        f"Джерело: {route.get('label')}\n"
        + (f"Правила відбору: {rules}\n" if rules else "")
        + f"Сирі дані:\n{str(raw)[:3500]}\n\n"
        "Витягни ТІЛЬКИ релевантні факти. Короткі рядки українською, без коментарів, "
        "без вигаданих значень. Якщо релевантного нема — поверни рівно: -"
    )
    out = await _safe_chat([{"role": "user", "content": user}], model, base_url, api_key, 0.0, 350, retry=False)
    out = (out or "").strip()
    emit(f"CLEAN #{step}", "Очищено", out or "-")
    return "" if out in ("-", "") else out
