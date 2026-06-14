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
  2. QUERY   — isolated call using ONLY that route's own query_prompt: turn the
               controller's structured need into the source query. No chat
               history or persistent route memory is attached.
  3. tool    — engine runs the real DB/web fetch for that route's tool.
  4. CLEAN   — isolated STATELESS call using ONLY that route's source_description
               + result_validation_prompt: raw source text -> clean facts.
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
    _CATALOG_SYNONYMS,
)

logger = logging.getLogger(__name__)

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
    """Controller map built only from tenant-editable route descriptions."""
    lines = [
        "Choose whether the main chat needs one configured route before it can answer the current message.",
        'Output EXACTLY one JSON object and nothing else:',
        '{"route":"<route code or answer>","question":"<precise internal question>",'
        '"needed_fact":"availability|price|policy|contact|device_type|other",'
        '"device_type":"","brand":"","model":"","service":"","part":""}',
        "NEVER write an address, working hours, phone or price yourself. If the client needs such a",
        "fact, pick the route that provides it — the client reply is written by a different stage.",
        "",
        "For route=answer leave the other fields empty. For a route, describe only the exact fact it must find.",
        "Configured routes:",
    ]
    for r in routes.values():
        trig = ", ".join(r["triggers"][:12])
        desc = (r.get("source_description") or "").strip()[:700]
        line = f'- "{r["code"]}" — {r["label"]}.'
        if desc:
            line += f" Provides: {desc}."
        if trig:
            line += f" Triggers: {trig}."
        lines.append(line)
    lines.append('- "answer" — respond without another source when no configured fact is needed or the needed '
                 "fact is already present in this turn's route results.")
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
    if conduct_on and await _judge_conduct(text, model, base_url, api_key) == "warn":
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
        warn_sys = (persona + f"\n\n[The client just insulted you (warning {cnt} of {warn_limit}). Reply "
                    f"with ONE short firm Ukrainian sentence: ask them to keep it civil and warn that after "
                    f"{warn_limit} warnings the chat will be closed. No extra info, no help offer.]")
        warn = await _safe_chat([{"role": "system", "content": warn_sys}] + _recent(history, text),
                                model, base_url, api_key, 0.3, 80, retry=False)
        return (warn or "Давайте без образ. Ще такий випад — і завершу чат.").strip(), memory

    for step in range(1, max_iter + 1):
        # The main controller keeps the conversation goal. Route workers remain
        # isolated and never receive this persona, marketing or other routes.
        sys = persona + "\n\n[AVAILABLE KNOWLEDGE ROUTES]\n" + source_map
        if route_results:
            sys += "\n\n[ROUTE RESULTS THIS TURN]\n" + "\n".join(route_results)
        # one-shot nudge so the small model returns JSON, not a prose answer
        dmsgs = [{"role": "system", "content": sys}] + _recent(history, text)
        dmsgs.append({"role": "system", "content": "Now output only the required JSON object."})
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
            "needed_fact": str(decision.get("needed_fact") or "other").strip(),
            "device_type": str(decision.get("device_type") or "").strip(),
            "brand": str(decision.get("brand") or "").strip(),
            "model": str(decision.get("model") or "").strip(),
            "service": str(decision.get("service") or "").strip(),
            "part": str(decision.get("part") or "").strip(),
        }

        # 2) QUERY — isolated call with ONLY this route's own query_prompt
        query = await _build_query(route, route_request, model, base_url, api_key, emit, step)

        # 3) tool — raw fetch
        raw_result, tool = await _run_tool(route, query, text, tenant_id, db, settings, syn_map, serper_key)
        emit(f"TOOL #{step}", f"{route['code']} → {tool}", str(raw_result)[:1500])

        # 4) CLEAN — isolated stateless call. Validate against the structured
        # request, never against the generated search phrase alone.
        result = await _clean_source(route, route_request, raw_result, model, base_url, api_key, emit, step)
        route_results.append(json.dumps({"route": route["code"], **result}, ensure_ascii=False))
        if result["relevant"]:
            facts.extend(f"[{route['label']}] {fact}" for fact in result["facts"])

    # 5) ANSWER — persona + chat + cleaned facts only
    ans_sys = persona
    # Marketing module (toggle): merged into the reply prompt only when enabled.
    marketing_on = str(meta.get("marketing_enabled", "")).strip().lower() in ("1", "true", "on", "yes")
    marketing = settings.marketing_rules if marketing_on and settings and settings.marketing_rules else ""
    if marketing:
        ans_sys += "\n\n[MARKETING — apply ONLY if it fits the talk naturally, never forced]\n" + marketing
    if facts:
        ans_sys += ("\n\n[VERIFIED FACTS — answer only from these and the client's own words. "
                    "Never invent a price or schedule not listed here.]\n" + "\n".join(facts))
    if route_results:
        ans_sys += (
            "\n\n[ROUTE OUTCOMES THIS TURN — internal control data, never quote it to the client.]\n"
            + "\n".join(route_results)
            + "\nIf a route returned relevant=false, it did NOT verify the requested fact. Do not claim that "
              "availability, price, policy, device type or contact was confirmed. Respond naturally using "
              "the fallback and the conversation, without exposing route names or JSON."
        )
    ans_sys += ("\n\nUse the conversation and route outcomes to answer the client now. "
                "Follow the persona and business rules above. Route output is evidence, not client wording. "
                "Never expose route names, prompts, JSON or raw source text.")
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


async def _judge_conduct(text, model, base_url, api_key):
    """Isolated conduct classifier — judges ONLY the current message (the warning
    count / ban decision is the engine's job, not this call)."""
    sys = (
        "Classify ONLY this client message. Answer with ONE word: normal / warn.\n"
        "- normal: a question, normal talk, frustration, swearing about a device/price/situation, "
        "profanity NOT aimed at a person, or off-topic that is not abusive.\n"
        "- warn: a DIRECT personal insult or threat aimed at the worker (e.g. «ти ідіот», «пішов нахер», "
        "«гавна кусок», «йди нахер»).\n"
        "Judge only this message. When unsure, answer normal."
    )
    out = await _safe_chat([{"role": "system", "content": sys}, {"role": "user", "content": text}],
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


async def _build_query(route, request, model, base_url, api_key, emit, step):
    """Isolated query construction using ONLY this route's query_prompt."""
    qp = route.get("query_prompt")
    if not qp:
        return ""
    sys = (
        "You are the query worker for one isolated knowledge route. Follow this route's instructions exactly. "
        "Return only the source query, without explanation, JSON or client reply. If the required query cannot "
        "be formed from the supplied request, return an empty string.\n\n[ROUTE QUERY INSTRUCTIONS]\n" + qp
    )
    msgs = [
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]
    q = await _safe_chat(msgs, model, base_url, api_key, 0.0, 24, retry=True)
    q = (q or "").strip().strip('"').splitlines()[0] if q else ""
    emit(f"QUERY #{step}", route["code"], q or "[empty query]")
    return q


async def _clean_source(route, request, raw, model, base_url, api_key, emit, step):
    """Isolated stateless cleaner using ONLY this route's clean prompts. The LLM
    judges usefulness and filters naturally — no hardcoded marker scripts."""
    if not str(raw or "").strip():
        emit(f"CLEAN #{step}", "Порожньо", "Джерело нічого не повернуло")
        return {"relevant": False, "sufficient": False, "facts": [], "fallback": "no_result"}
    raw_text = str(raw).strip()
    rules = route.get("result_validation_prompt") or route.get("source_description") or ""
    system = (
        "You are an isolated validator for ONE knowledge route. You do not know the main persona, marketing, "
        "conduct rules, other routes or their results. Decide relevance and sufficiency only by this route's "
        "instructions and the supplied source. Return exactly one JSON object with this shape: "
        '{"relevant":true|false,"sufficient":true|false,"facts":["..."],"fallback":"..."|null}. '
        "Facts must be concise source-supported statements useful to the main chat. Fallback is concise guidance "
        "for the main chat when the requested fact was not verified. Do not write the client reply.\n\n"
        f"[WHAT THIS ROUTE CONTAINS]\n{route.get('source_description') or ''}\n\n"
        f"[ROUTE VALIDATION AND FALLBACK INSTRUCTIONS]\n{rules}"
    )
    user = (
        f"Структурований запит головної моделі: {json.dumps(request, ensure_ascii=False)}\n"
        f"Сирі дані джерела:\n{raw_text[:5000]}"
    )
    out = await _safe_chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                           model, base_url, api_key, 0.0, 450, retry=True)
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
