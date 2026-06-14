"""
Lean isolated-call agent (engine = "lean").

Owner's design — stop dragging one fat context through every model call. Each
stage is its OWN isolated call with only its own small prompt and only the data
it needs. Everything (routes, their prompts, the knowledge bases, the persona,
search) already exists in the DB — this module only fixes the LOGIC and splits
the memory:

  1. DECIDE  — a COMPACT map built from the tenant's own routes + chat + route
               outcomes from this turn. One job: create a structured internal
               question and pick a route or answer. Small -> reliable JSON.
  2. ROUTE   — a private LLM session receives only that route's three prompts
               and the controller's structured request. Its first turn builds
               the query; the tool result returns into the same local memory;
               its second turn validates and filters the result.
  3. tool    — engine runs the real DB/web fetch selected by that route.
  4. result  — the route session returns clean facts/fallback, then its private
               memory is discarded.
  5. ANSWER  — persona + chat + the cleaned facts -> the client reply.

Only cleaned facts move forward; raw bases never enter the main context.
"""
import json
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
)
from app.core.prompt_defaults import DEFAULT_UNIVERSAL_PERSONA

logger = logging.getLogger(__name__)

CONTROLLER_OUTPUT_SCHEMA = {
    "route": "<route code or answer>",
    "question": "",
    "requested_fact": "",
    "subject": "",
    "identifier": "",
    "operation": "",
    "qualifiers": {},
}

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
    """Serialize tenant route metadata; decisions belong to the configured prompt."""
    lines = []
    for r in routes.values():
        trig = ", ".join(r["triggers"][:12])
        desc = (r.get("source_description") or "").strip()[:700]
        line = f'- "{r["code"]}" — {r["label"]}.'
        if desc:
            line += f" Provides: {desc}."
        if trig:
            line += f" Triggers: {trig}."
        lines.append(line)
    return "\n".join(lines)


_TOOL_BY_HANDLER = {"qa_handler": "search_catalog", "web_search_handler": "web_research", "site_search": "open_url"}


async def _run_tool(route, query, text, tenant_id, db, settings, syn_map, serper_key):
    tool = route.get("tool_name") or _TOOL_BY_HANDLER.get(route.get("handler"), "")
    q = (query or "").strip()
    if not q and tool != "get_business_info":
        return "", tool
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
        meta = (settings.meta or {}) if settings else {}
        if tool == "search_parts":
            from urllib.parse import quote
            from app.core.tools import fetch_and_parse_url
            direct = []
            for template in str(meta.get("price_search_urls") or "").splitlines()[:4]:
                template = template.strip()
                if not template:
                    continue
                url = template.replace("{query}", quote(q)) if "{query}" in template else template
                page = await asyncio.to_thread(fetch_and_parse_url, url, 1800)
                if page:
                    direct.append(f"SOURCE {url}\n{page}")
            if direct:
                return "\n\n".join(direct), tool
            sites = [s.strip() for s in str(meta.get("parts_sites") or "").replace("\n", ",").split(",") if s.strip()]
            if sites:
                q = f"({' OR '.join(f'site:{site}' for site in sites)}) {q}"
        return await asyncio.to_thread(web_research, q, 3, 3000, serper_key), tool
    if tool == "open_url":
        import asyncio
        from urllib.parse import quote
        from app.core.tools import fetch_and_parse_url, web_research
        url = route.get("target_url", "")
        if "{query}" in url:
            url = url.replace("{query}", quote(q))
        if url.startswith("http"):
            return await asyncio.to_thread(fetch_and_parse_url, url), tool
        if url:
            domain = url.replace("https://", "").replace("http://", "").strip("/")
            return await asyncio.to_thread(web_research, f"site:{domain} {q}", 3, 3000, serper_key), tool
        return "", tool
    if tool == "escalate":
        handoff = (settings.escalation_prompt or "").strip() if settings else ""
        if handoff:
            return f"Configured human-contact guidance: {handoff}", tool
        return "No confirmed transfer integration or configured human-contact guidance.", tool
    return "", tool


async def run_agent_lean(text, history, tenant_id, db, settings, trace=None, memory=None):
    emit = trace or (lambda *a, **k: None)
    memory = dict(memory or {})
    meta = (settings.meta or {}) if settings else {}

    persona = (settings.system_prompt if settings and settings.system_prompt
               else DEFAULT_UNIVERSAL_PERSONA)
    business_rules = settings.business_rules if settings and settings.business_rules else ""
    if business_rules:
        persona += "\n\n[BUSINESS RULES]\n" + business_rules

    base_url = meta.get("llm_base_url")
    api_key = meta.get("llm_api_key")
    model = settings.llm_model if settings and settings.llm_model else "gemma-4"
    controller_prompt = str(meta.get("lean_controller_prompt") or "").strip()
    answer_prompt = str(meta.get("lean_answer_prompt") or "").strip()
    conduct_prompt = str(meta.get("lean_conduct_prompt") or "").strip()
    warning_prompt = str(meta.get("lean_warning_prompt") or "").strip()
    syn_map = _parse_synonyms_map(meta.get("catalog_synonyms"), {})
    serper_key = meta.get("serper_api_key")
    try:
        max_iter = min(3, max(1, int(meta.get("agent_max_iterations", 3))))
    except (ValueError, TypeError):
        max_iter = 3

    routes = await _load_routes(tenant_id, db)
    source_map = _source_map(routes)

    # Route results exist only inside this turn. Conversation continuity comes
    # from chat history; facts from an old device/topic must not leak into a new
    # request. Session memory is reserved for conduct/ban state.
    memory.pop("_facts", None)
    facts = []
    route_results = []
    done = set()

    # --- CONDUCT MODULE (toggle) — counts warnings, bans after the limit ---
    ban_msg = (meta.get("ban_message") or "Вітаю, вас забанено.").strip()
    if memory.get("_session_banned") == "1":
        return ban_msg, memory
    conduct_on = str(meta.get("conduct_enabled", "1")).strip().lower() not in ("0", "false", "off", "no", "")
    try:
        warn_limit = max(1, int(meta.get("conduct_warnings", 2)))
    except (ValueError, TypeError):
        warn_limit = 2
    if conduct_on and conduct_prompt and await _judge_conduct(text, conduct_prompt, model, base_url, api_key) == "warn":
        try:
            cnt = int(memory.get("_warn_count") or 0) + 1
        except (ValueError, TypeError):
            cnt = 1
        memory["_warn_count"] = str(cnt)
        memory["_conduct_warning"] = "1"
        if cnt > warn_limit:
            memory["_session_banned"] = "1"
            emit("CONDUCT", "БАН", f"перевищено {warn_limit} попереджень")
            return ban_msg, memory
        emit("CONDUCT", "Попередження", f"{cnt}/{warn_limit}")
        warn_sys = persona + "\n\n" + warning_prompt.replace("{warning_count}", str(cnt)).replace("{warning_limit}", str(warn_limit))
        warn = await _safe_chat([{"role": "system", "content": warn_sys}] + _recent(history, text),
                                model, base_url, api_key, 0.3, 80, retry=False)
        return (warn or (settings.fallback_text if settings else "") or "Технічна заминка, спробуйте ще раз.").strip(), memory

    for step in range(1, max_iter + 1):
        # The main controller keeps the conversation goal. Route workers remain
        # isolated and never receive this persona, marketing or other routes.
        sys = (persona + "\n\n" + controller_prompt +
               "\n\n[OUTPUT JSON SCHEMA]\n" +
               json.dumps(CONTROLLER_OUTPUT_SCHEMA, ensure_ascii=False) +
               "\n\n[AVAILABLE KNOWLEDGE ROUTES]\n" + source_map)
        if route_results:
            sys += "\n\n[ROUTE RESULTS THIS TURN]\n" + "\n".join(route_results)
        # one-shot nudge so the small model returns JSON, not a prose answer
        dmsgs = [{"role": "system", "content": sys}] + _recent(history, text)
        raw = await _safe_chat(dmsgs, model, base_url, api_key, 0.0, 180, retry=True)
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

        route_request = {
            "question": str(decision.get("question") or text).strip(),
            "requested_fact": str(decision.get("requested_fact") or "").strip(),
            "subject": str(decision.get("subject") or "").strip(),
            "identifier": str(decision.get("identifier") or "").strip(),
            "operation": str(decision.get("operation") or "").strip(),
            "qualifiers": decision.get("qualifiers") if isinstance(decision.get("qualifiers"), dict) else {},
        }

        # The route owns one isolated LLM session. Its first turn creates the
        # source query; the raw tool result is then returned to the SAME local
        # message list for validation. Persona/chat/marketing/other routes never
        # enter this memory, and the memory is discarded after this route call.
        result = await _run_route_session(
            route, route_request, text, tenant_id, db, settings, syn_map,
            serper_key, model, base_url, api_key, emit, step,
        )
        route_results.append(json.dumps({"route": route["code"], **result}, ensure_ascii=False))
        if result["relevant"]:
            facts.extend(f"[{route['label']}] {fact}" for fact in result["facts"])

    # 5) ANSWER — persona + chat + cleaned facts only
    ans_sys = persona
    # Marketing module (toggle): merged into the reply prompt only when enabled.
    marketing_on = str(meta.get("marketing_enabled", "")).strip().lower() in ("1", "true", "on", "yes")
    marketing = settings.marketing_rules if marketing_on and settings and settings.marketing_rules else ""
    if marketing:
        ans_sys += "\n\n[MARKETING PROMPT]\n" + marketing
    if route_results:
        ans_sys += "\n\n[ROUTE RESULTS THIS TURN]\n" + "\n".join(route_results)
    ans_sys += "\n\n" + answer_prompt
    amsgs = [{"role": "system", "content": ans_sys}] + _recent(history, text)
    try:
        temp = float(settings.temperature) if settings and settings.temperature else 0.3
    except (ValueError, TypeError):
        temp = 0.3
    try:
        answer_tokens = min(1200, max(120, int(settings.max_tokens or 700))) if settings else 700
    except (ValueError, TypeError):
        answer_tokens = 700
    answer = await _safe_chat(amsgs, model, base_url, api_key, temp, answer_tokens, retry=False)
    if not answer:
        answer = settings.fallback_text if settings and settings.fallback_text else "Технічна заминка, спробуйте ще раз."
    answer = _clean_answer(answer, fallback=(settings.fallback_text if settings else "") or "")

    emit("ANSWER", "OK", f"Перевірених фактів цього звернення: {len(facts)}")
    return answer, memory


async def _judge_conduct(text, prompt, model, base_url, api_key):
    """Isolated conduct classifier — judges ONLY the current message (the warning
    count / ban decision is the engine's job, not this call)."""
    out = await _safe_chat([{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                           model, base_url, api_key, 0.0, 4, retry=False)
    return "warn" if "warn" in (out or "").lower() else "normal"


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


async def _run_route_session(route, request, text, tenant_id, db, settings, syn_map,
                             serper_key, model, base_url, api_key, emit, step):
    """Run one route-owned LLM session with short-lived private memory."""
    system = (
        f"[ROUTE SOURCE]\n{route.get('source_description') or ''}\n\n"
        f"[HOW THIS ROUTE BUILDS ITS SOURCE QUERY]\n{route.get('query_prompt') or ''}\n\n"
        f"[HOW THIS ROUTE VALIDATES AND FILTERS RESULTS]\n{route.get('result_validation_prompt') or ''}"
    )
    route_memory = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            "PHASE: BUILD_SOURCE_QUERY\n"
            f"REQUEST: {json.dumps(request, ensure_ascii=False)}\n"
            'Return JSON only: {"query":"..."}'
        )},
    ]
    query_raw = await _safe_chat(route_memory, model, base_url, api_key, 0.0, 80, retry=True)
    try:
        query = str(_extract_json(query_raw).get("query") or "").strip()
    except Exception:
        query = ""
    emit(f"QUERY #{step}", route["code"], query or "[empty query]")

    raw_result, tool = await _run_tool(route, query, text, tenant_id, db, settings, syn_map, serper_key)
    emit(f"TOOL #{step}", f"{route['code']} → {tool}", str(raw_result)[:1500])

    route_memory.append({"role": "assistant", "content": query_raw or '{"query":""}'})
    route_memory.append({"role": "user", "content": (
        "PHASE: VALIDATE_SOURCE_RESULT\n"
        f"REQUEST: {json.dumps(request, ensure_ascii=False)}\n"
        f"SOURCE_RESULT:\n{str(raw_result or '')[:5000]}\n"
        'Return JSON only: {"relevant":true|false,"sufficient":true|false,'
        '"facts":["..."],"fallback":"..."|null}'
    )})
    out = await _safe_chat(route_memory, model, base_url, api_key, 0.0, 450, retry=True)
    out = (out or "").strip()
    try:
        parsed = _extract_json(out)
        facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
        facts = [str(f).strip() for f in facts if str(f).strip()]
        relevant = parsed.get("relevant") is True
        result = {
            "relevant": relevant,
            "sufficient": relevant and parsed.get("sufficient") is True,
            "facts": facts if relevant else [],
            "fallback": str(parsed.get("fallback")).strip() if parsed.get("fallback") else None,
        }
    except Exception:
        result = {"relevant": False, "sufficient": False, "facts": [], "fallback": "validation_failed"}
    emit(f"CLEAN #{step}", "Очищено", json.dumps(result, ensure_ascii=False))
    return result
