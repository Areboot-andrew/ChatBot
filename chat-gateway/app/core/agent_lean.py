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
               client's need into the source query. (Each route searches with
               its own prompt, its own little memory.)
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
    """Strict router map from the tenant's own routes — a few lines, no essays."""
    lines = [
        "You are a ROUTER. You are NOT the assistant. You NEVER write a message to the client here.",
        'Output EXACTLY one JSON object and nothing else:',
        '{"route":"<route code or answer>","question":"<precise internal question>",'
        '"needed_fact":"availability|price|policy|contact|device_type|other",'
        '"device_type":"","brand":"","model":"","service":"","part":""}',
        "NEVER write an address, working hours, phone or price yourself. If the client needs such a",
        "fact, pick the route that provides it — the client reply is written by a different stage.",
        "",
        "Routes (pick the one whose data answers the client's CURRENT message):",
    ]
    for r in routes.values():
        trig = ", ".join(r["triggers"][:6])
        # one compact line of what this source provides — from the route's own
        # source_description (editable in Схема Логіки), so routing is prompt-driven.
        desc = (r.get("source_description") or "").strip()
        desc = desc.split(".")[0][:160] if desc else ""
        line = f'- "{r["code"]}" — {r["label"]}.'
        if desc:
            line += f" Provides: {desc}."
        if trig:
            line += f" Triggers: {trig}."
        lines.append(line)
    lines.append('- "answer" — ONLY for a greeting, small talk, off-topic, or when the needed fact is '
                 "already in [ROUTE RESULTS THIS TURN]. Never pick answer for a fact you still need.")
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
        # 1) DECIDE — ONLY the route map + chat + cleaned facts. No persona, no
        # behaviour/tone/conduct rules — routing doesn't need them, and dragging
        # them is exactly what bloated the context. Tone lives only in ANSWER.
        sys = source_map
        if route_results:
            sys += "\n\n[ROUTE RESULTS THIS TURN]\n" + "\n".join(route_results)
        # one-shot nudge so the small model returns JSON, not a prose answer
        dmsgs = [{"role": "system", "content": sys}] + _recent(history, text)
        dmsgs.append({"role": "system", "content": "Now output only the required JSON object."})
        raw = await _safe_chat(dmsgs, model, base_url, api_key, 0.0, 60, retry=True)
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
    # The business's own contact card — tiny, always available so the model can
    # never invent an address/hours/phone (rule #1). Real data, not a base dump.
    biz = meta.get("business_info") if isinstance(meta.get("business_info"), dict) else None
    if biz:
        biz_lines = "\n".join(f"- {k}: {v}" for k, v in biz.items() if str(v).strip())
        if biz_lines:
            ans_sys += ("\n\n[BUSINESS CONTACTS — the ONLY source for address, hours, phone, payment, "
                        "delivery; use these exact values, never invent. Give ONLY the single fact the "
                        "client actually asked for: if they asked where to bring it, give just the address. "
                        "Do NOT volunteer delivery, payment, hours or phone unless they asked for them.]\n" + biz_lines)
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
    ans_sys += (
        "\n\n[REPLY RULE] Talk like a real repair master — short, natural, human. Answer only what the "
        "client actually asked, in 1-2 sentences.\n"
        "- Price asked: give a natural range from the facts (напр. «від 900 до 3600 грн залежно від "
        "несправності»), say the exact price is after the free diagnostics. NEVER list the whole price "
        "table — pick only the rows that fit, summarise as one range.\n"
        "- Work and part are separate: state our work price and the part separately, never merged.\n"
        "- 'Do you repair X': confirm only when VERIFIED FACTS explicitly support availability. If the "
        "availability route returned relevant=false, say it is not confirmed; never infer it from a broad "
        "category or general knowledge. A bare device/model without an availability question means ask what "
        "is wrong, not automatically confirm repair. Mention diagnostics conditions only when verified.\n"
        "- FAQ/policy: answer in one plain sentence from the facts; don't quote raw database wording.\n"
        "- Always reply in Ukrainian only. Never output English or any internal note to the client.\n"
        "- Never invent a number, address or term not in the facts, and don't pile on info nobody asked for."
    )
    amsgs = [{"role": "system", "content": ans_sys}] + _recent(history, text)
    try:
        temp = float(settings.temperature) if settings and settings.temperature else 0.3
    except (ValueError, TypeError):
        temp = 0.3
    answer = await _safe_chat(amsgs, model, base_url, api_key, temp, 700, retry=False)
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
    sys = ("Output ONLY 2-5 search keywords for one source — no sentence, no question, no answer, "
           "no numbers/prices/hours of your own, no quotes, no JSON. Just the keywords.\n"
           "Guidance: " + qp)
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
    explicit_no_result = (
        "немає рядка або категорії, що збігається із запитом",
        "нічого не знайдено у базі знань",
        "бізнес-інформація не налаштована",
    )
    if any(marker in raw_text.lower() for marker in explicit_no_result):
        result = {"relevant": False, "sufficient": False, "facts": [], "fallback": "no_result"}
        emit(f"CLEAN #{step}", "Немає збігу", json.dumps(result, ensure_ascii=False))
        return result
    rules = route.get("result_validation_prompt") or route.get("source_description") or ""
    user = (
        f"Структурований запит головної моделі: {json.dumps(request, ensure_ascii=False)}\n"
        f"Джерело: {route.get('label')}\n"
        + (f"Правила відбору: {rules}\n" if rules else "")
        + f"Сирі дані:\n{raw_text[:3500]}\n\n"
        "Return ONLY JSON: {\"relevant\":boolean,\"sufficient\":boolean,\"facts\":[string],"
        "\"fallback\":string|null}. A fact is allowed only when the source explicitly supports the same "
        "device/item type and requested fact. Never place an item into a broad category by your own world "
        "knowledge. A list of unrelated categories is not evidence. Reject another category even when it "
        "shares a component word (phone/TV/column speaker != headphones). Use only numbers present in the "
        "raw data. If there is no explicit matching evidence, relevant=false, facts=[], fallback=\"no_result\"."
    )
    out = await _safe_chat([{"role": "user", "content": user}], model, base_url, api_key, 0.0, 350, retry=False)
    out = (out or "").strip()
    try:
        parsed = _extract_json(out)
        facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
        facts = [str(f).strip() for f in facts if str(f).strip()]
        relevant = parsed.get("relevant") is True and bool(facts)
        result = {
            "relevant": relevant,
            "sufficient": relevant and parsed.get("sufficient") is True,
            "facts": facts if relevant else [],
            "fallback": None if relevant else str(parsed.get("fallback") or "no_result"),
        }
    except Exception:
        result = {"relevant": False, "sufficient": False, "facts": [], "fallback": "validation_failed"}
    emit(f"CLEAN #{step}", "Очищено", json.dumps(result, ensure_ascii=False))
    return result
