"""
Isolated-call conversation pipeline.

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
  3. tool    — pipeline runs the real DB/web fetch selected by that route.
  4. result  — the route session returns clean facts/fallback, then its private
               memory is discarded.
  5. ANSWER  — persona + chat + the cleaned facts -> the client reply.

Only cleaned facts move forward; raw bases never enter the main context.
"""
import json
import logging
import re

from sqlalchemy import select

from app.core.llm import chat
from app.config import normalize_lmstudio_url
from app.core.route_tools import (
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

_CONDUCT_PRECHECK_RE = re.compile(
    r"(?iu)(?:"
    r"\b(?:нах(?:уй|ер|рін|рен)|хуй|ху[яєею]|пизд|пізд|єба|еба|йоб|їб|бляд|сука)\w*\b"
    r"|(?:іди|йди|пішов|пішла|пішли)\s+на\s*х"
    r"|\b(?:дур(?:ак|епа)|ідіот|дебіл|тварина)\b"
    r")"
)

CONTROLLER_OUTPUT_SCHEMA = {
    "route": "<route code or answer>",
    "question": "",
    "requested_fact": "",
    "subject": "",
    "identifier": "",
    "operation": "",
    "qualifiers": {},
}

def _ua_fallback(settings) -> str:
    """A real Ukrainian fallback that never returns the literal 'None' even if the
    tenant's fallback_text got corrupted in the DB."""
    ft = (settings.fallback_text if settings and settings.fallback_text else "").strip()
    stale_defaults = {
        "вибачте, зараз не можу відповісти — спробуйте ще раз трохи згодом.",
        "вибачте, сталася технічна помилка.",
        "технічна заминка, спробуйте ще раз.",
        "service temporarily unavailable.",
    }
    if ft.lower() in ("none", "null", "undefined", "nil", "-") or ft.lower() in stale_defaults:
        ft = ""
    if ft:
        return ft

    meta = (settings.meta or {}) if settings else {}
    business_info = meta.get("business_info") if isinstance(meta.get("business_info"), dict) else {}
    phone = str((business_info or {}).get("phone") or "").strip()
    msg = "Зараз технічна заминка з відповіддю. Напишіть ще раз за хвилину"
    if phone:
        msg += f" або подзвоніть: {phone}"
    return msg + "."


def _decision_from_raw(raw: str):
    """Parse the controller decision tolerantly. Small models (llama) sometimes
    emit slightly broken JSON (stray comma, extra quote). Returns (decision,
    recovered, usable): if strict JSON fails we still pull the route code + key
    fields by regex instead of dropping the whole routing decision. If neither
    JSON nor a route field exists, the controller output is unusable and a
    structural fallback may rescue the turn."""
    raw = raw or ""
    try:
        parsed = _extract_json(raw)
        return parsed, False, bool(str(parsed.get("route") or parsed.get("action") or "").strip())
    except Exception:
        pass
    m = re.search(r'"route"\s*:\s*"?([a-zA-Z_][\w]*)"?', raw)
    if not m:
        return {"route": "answer"}, False, False
    d = {"route": m.group(1)}
    for k in ("question", "requested_fact", "subject", "identifier", "operation"):
        mm = re.search(r'"%s"\s*:\s*"([^"]*)"' % k, raw)
        if mm:
            d[k] = mm.group(1)
    return d, True, bool(str(d.get("route") or "").strip())


def _text_blob(text: str, history: list | None = None) -> str:
    recent = " ".join(str(h.get("content", "")) for h in (history or [])[-6:])
    return f"{recent} {text or ''}".lower()


def _recent_user_context(text: str, history: list | None, limit: int = 4) -> str:
    """Compact topic memory for fallback routing only.

    When the controller fails on follow-ups like "так хоч орієнтовно ціну",
    the current sentence alone has no subject. Use only recent user words, not
    assistant replies or route data, so the fallback still does not invent facts.
    """
    skip = re.compile(r"(?iu)^\s*(привіт|добрий\s+день|доброго|ок|так|ні|дякую|спасибі)\W*$")
    chunks = []
    for h in (history or [])[-8:]:
        if h.get("role") != "user":
            continue
        content = str(h.get("content", "")).strip()
        if content and not skip.match(content):
            chunks.append(content)
    current = (text or "").strip()
    if current and (not chunks or chunks[-1] != current):
        chunks.append(current)
    return " ".join(chunks[-limit:])[:260]


def _looks_like_scope_followup(text: str, history: list | None) -> bool:
    """Catch human follow-ups such as "а телефони??".

    The client often omits "ремонтуєте/робите" after the first scope question.
    If the controller returns empty, a short noun-like question must be checked
    in catalog instead of letting the answer model guess yes/no.
    """
    current = (text or "").strip().lower()
    if not current or len(current) > 90:
        return False
    if re.fullmatch(r"(?iu)[\W_]*(привіт|добрий\s+день|доброго|ок|так|ні|дякую|спасибі|ага|угу)[\W_]*", current):
        return False
    tokens = [
        w for w in re.findall(r"(?iu)[a-zа-щьюяіїєґ0-9]{3,}", current)
        if w not in {"мені", "вам", "вас", "таке", "його", "вона", "вони", "ціна", "скільки"}
    ]
    if not tokens:
        return False
    if re.search(r"(?iu)\b(не\s+працю|не\s+вмика|не\s+заряд|розби|злама|впав|залив|вода|дим|іскр|шум|тече)\w*", current):
        return True
    if re.search(r"(?iu)\b(в|на)\s+ремонт\b|\bремонт\??\s*$", current):
        return True
    if "?" in current and (current.startswith(("а ", "і ", "и ", "та ", "ще ")) or len(tokens) <= 4):
        return True
    recent_user = " ".join(
        str(h.get("content", "")).lower()
        for h in (history or [])[-4:]
        if h.get("role") == "user"
    )
    return bool(re.search(r"(?iu)\b(робите|ремонтуєте|берете|приймаєте|займаєтесь)\b|\?", recent_user)) and len(tokens) <= 4


def _fallback_route_decision(text: str, history: list | None, routes: dict):
    """Structural safety net when the controller returns prose/empty output.

    This is not business logic and does not decide facts. It only prevents a
    failed controller from falling into free-form ANSWER for requests whose fact
    owner is obvious from route roles: scope/catalog, price, business fields, or
    policy/process knowledge.
    """
    current = (text or "").strip()
    current_l = current.lower()
    blob = _text_blob(text, history)

    def has_route(code: str) -> bool:
        return code in routes and bool(routes[code].get("tool_name"))

    scope_re = (
        r"\b(ремонтуєте|ремонтуємо|робите|берете|приймаєте|займаєтесь|"
        r"обслуговуєте|чините|можна\s+принести|можна\s+привезти)\b|"
        r"що\s+(ви\s+)?(ремонтуєте|робите|берете|обслуговуєте|приймаєте)|"
        r"\b(в|на)\s+ремонт\b"
    )
    active_scope_re = (
        r"що\s+(ви\s+)?(ремонтуєте|робите|берете|обслуговуєте|приймаєте)|"
        r"яку\s+техніку|які\s+пристрої|чим\s+займаєтесь"
    )
    price_re = r"\b(ціна|вартість|скільки\s+коштує|по\s+чому|прайс|орієнтовно)\b"
    business_re = (
        r"\b(адрес\w*|де\s+ви|графік|коли\s+працю|години|номер|контакт\w*|"
        r"ваш\s+телефон|телефон\s+для\s+зв'?язку|подзвон|зателефон|"
        r"оплата|заплатити|нова\s+пошта|відправити|доставка|гарантія)\b"
    )
    qa_re = r"\b(як\s+відбувається|умови|правила|процес|запчастини\s+окремо|гарантійний)\b"

    if has_route("catalog") and re.search(price_re, current_l):
        return {
            "route": "catalog",
            "question": current,
            "requested_fact": "price",
            "subject": _recent_user_context(text, history),
            "operation": "tenant_price_lookup",
            "identifier": "",
            "qualifiers": {},
        }, "price keywords"
    if has_route("business_info") and re.search(business_re, current_l):
        return {
            "route": "business_info",
            "question": current,
            "requested_fact": "business_info",
            "subject": current,
            "operation": "lookup_business_field",
            "identifier": "",
            "qualifiers": {},
        }, "business field keywords"
    if has_route("catalog") and (
        re.search(scope_re, blob)
        or re.search(active_scope_re, blob)
        or _looks_like_scope_followup(text, history)
    ):
        return {
            "route": "catalog",
            "question": current,
            "requested_fact": "availability",
            "subject": current,
            "operation": "scope_check",
            "identifier": "",
            "qualifiers": {},
        }, "scope/availability context"
    if has_route("qa") and re.search(qa_re, current_l):
        return {
            "route": "qa",
            "question": current,
            "requested_fact": "policy_or_process",
            "subject": current,
            "operation": "knowledge_lookup",
            "identifier": "",
            "qualifiers": {},
        }, "knowledge/policy keywords"
    return None, ""


def _fmt_msgs(messages) -> str:
    """Render the exact messages sent to the model for the live trace."""
    return "\n\n".join(f"[{m.get('role', '?').upper()}]\n{m.get('content', '')}" for m in messages)


def _conduct_warning_fallback(warning_count: int, warning_limit: int) -> str:
    if warning_limit <= 1:
        return "Я не продовжуватиму розмову в такому тоні."
    return "Я не продовжуватиму розмову в такому тоні. Наступна пряма образа — закрию чат."


def _needs_business_contacts(text: str, route_results: list[str], facts: list[str]) -> bool:
    """Only attach the tenant contact card when the current turn needs it."""
    current = (text or "").lower()
    if re.search(
        r"(?iu)\b(адрес\w*|де\s+ви|куди|графік|коли\s+працю|години|"
        r"номер|контакт\w*|ваш\s+телефон|телефон\s+для\s+зв'?язку|"
        r"подзвон|зателефон|оплата|заплатити|нова\s+пошта|відправити|"
        r"доставка|гарантія)\b",
        current,
    ):
        return True
    joined_routes = "\n".join(route_results).lower()
    if '"route": "business_info"' in joined_routes:
        return True
    return any("бізнес-інфо" in f.lower() or "business_info" in f.lower() for f in facts)


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
            "tool_name": (m.get("tool_name") or "").strip(),
            "triggers": list(kt.intent_patterns or []),
            "query_prompt": (m.get("query_prompt") or "").strip(),
            "source_description": (m.get("source_description") or "").strip(),
            "result_validation_prompt": (m.get("result_validation_prompt") or "").strip(),
            "target_url": (m.get("target_url") or "").strip(),
            "content_map": "",
        }
    for route in routes.values():
        if route.get("tool_name") == "search_catalog":
            route["content_map"] = await _catalog_content_map(tenant_id, db)
    return routes


async def _catalog_content_map(tenant_id, db) -> str:
    """Tiny category table of contents for routing.

    Keep this deliberately tiny. The controller should see only category
    headings, not prices, descriptions, brands, symptoms, or item details. Deep
    catalog rows are opened later by the selected route/tool.
    """
    try:
        from app.models.services import ServiceCategory

        res = await db.execute(
            select(ServiceCategory)
            .where(ServiceCategory.tenant_id == tenant_id, ServiceCategory.enabled == True)
            .order_by(ServiceCategory.title)
        )
        lines = []
        for cat in res.scalars().all()[:80]:
            title = (cat.title or "").strip()
            if title:
                lines.append(f"- {title}")
        return "\n".join(lines)[:2000]
    except Exception as e:
        logger.warning(f"catalog content map failed: {e}")
        return ""


def _source_map(routes: dict) -> str:
    """Serialize tenant route metadata; decisions belong to the configured prompt."""
    lines = []
    for r in routes.values():
        trig = ", ".join(r["triggers"][:12])
        line = f'- "{r["code"]}" — {r["label"]}.'
        if trig:
            line += f" Triggers: {trig}."
        if r.get("content_map"):
            line += f"\n  CONTENT MAP:\n{r['content_map']}"
        lines.append(line)
    return "\n".join(lines)


async def _run_tool(route, query, text, tenant_id, db, settings, syn_map, serper_key, requested_fact=""):
    tool = route.get("tool_name") or ""
    q = (query or "").strip()
    fact = (requested_fact or "").strip().lower()
    scope_only = fact in {"availability", "scope", "scope_check", "наявність", "наличие"}
    if tool == "search_catalog" and scope_only:
        return await _tool_list_categories(tenant_id, db), tool
    if not q and tool != "get_business_info":
        return "", tool
    if tool == "search_catalog":
        return await _tool_search_catalog(q, tenant_id, db, synonyms=syn_map, requested_fact=requested_fact), tool
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

    base_url = normalize_lmstudio_url(meta.get("llm_base_url"))
    api_key = meta.get("llm_api_key")
    model = settings.llm_model if settings and settings.llm_model else "gemma-4"
    controller_prompt = str(meta.get("lean_controller_prompt") or "").strip()
    answer_prompt = str(meta.get("lean_answer_prompt") or "").strip()
    conduct_prompt = str(meta.get("lean_conduct_prompt") or "").strip()
    warning_prompt = str(meta.get("lean_warning_prompt") or "").strip()
    syn_map = _parse_synonyms_map(meta.get("catalog_synonyms"), {})
    serper_key = meta.get("serper_api_key")
    # One client message should open at most one knowledge source. Multi-route
    # planning caused the assistant to dig through unrelated bases; the next
    # client turn can request the next missing fact naturally.
    max_iter = 1

    routes = await _load_routes(tenant_id, db)
    source_map = _source_map(routes)

    # Verified facts/steps persist in chat memory so the model remembers what it
    # already checked (e.g. that we do/don't repair a given item) across turns and
    # does not re-verify or assume. Seeded from memory, saved back (capped) at end.
    facts = [str(f) for f in (memory.get("_facts") or []) if str(f).strip()]
    route_results = []
    controller_status_notes = []
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
    conduct_warn = False
    if conduct_on and _conduct_precheck(text):
        conduct_warn = True
        emit("CONDUCT", "Швидкий скан", "очевидна лайка або пряма образа")
    if conduct_on and conduct_prompt:
        emit("CONDUCT", "Вхід у модель", _fmt_msgs([{"role": "system", "content": conduct_prompt}, {"role": "user", "content": text}]))
    if conduct_on and conduct_prompt and not conduct_warn:
        conduct_warn = await _judge_conduct(text, conduct_prompt, model, base_url, api_key) == "warn"
    if conduct_on and conduct_warn:
        try:
            cnt = int(memory.get("_warn_count") or 0) + 1
        except (ValueError, TypeError):
            cnt = 1
        memory["_warn_count"] = str(cnt)
        memory["_conduct_warning"] = "1"
        if cnt >= warn_limit:
            memory["_session_banned"] = "1"
            emit("CONDUCT", "БАН", f"досягнуто ліміт {warn_limit}")
            return ban_msg, memory
        emit("CONDUCT", "Попередження", f"{cnt}/{warn_limit}")
        warn_sys = persona + "\n\n" + warning_prompt.replace("{warning_count}", str(cnt)).replace("{warning_limit}", str(warn_limit))
        warn = await _safe_chat([{"role": "system", "content": warn_sys}] + _recent(history, text),
                                model, base_url, api_key, 0.3, 80, retry=False, emit=emit, label="WARNING")
        fallback_warning = _conduct_warning_fallback(cnt, warn_limit)
        return (warn.strip() if warn.strip() else fallback_warning), memory

    for step in range(1, max_iter + 1):
        sys = (controller_prompt +
               "\n\n[OUTPUT JSON SCHEMA]\n" +
               json.dumps(CONTROLLER_OUTPUT_SCHEMA, ensure_ascii=False) +
               "\n\n[AVAILABLE KNOWLEDGE ROUTES]\n" + source_map)
        if facts:
            sys += "\n\n[ALREADY VERIFIED EARLIER IN THIS CHAT]\n" + "\n".join(facts)
        if route_results:
            sys += "\n\n[ROUTE RESULTS THIS TURN]\n" + "\n".join(route_results)
        # one-shot nudge so the small model returns JSON, not a prose answer
        dmsgs = [{"role": "system", "content": sys}] + _recent(history, text)
        emit(f"DECIDE #{step}", "Вхід у модель", _fmt_msgs(dmsgs))
        raw = await _safe_chat(dmsgs, model, base_url, api_key, 0.0, 180, retry=True,
                               emit=emit, label=f"DECIDE #{step}")
        emit(f"DECIDE #{step}", "Сире рішення", str(raw) or "[порожньо]")
        decision, recovered, controller_usable = _decision_from_raw(raw)
        if recovered:
            emit(f"DECIDE #{step}", "JSON виправлено", "Контролер дав кривий JSON — route витягнуто толерантним парсером.")
        elif not str(raw or "").strip():
            emit(f"DECIDE #{step}", "Порожньо", "Контролер нічого не повернув (див. помилку LLM вище).")
            controller_status_notes.append(
                "The controller returned no usable decision this turn, so no new business fact was verified by a route."
            )
        elif not controller_usable:
            emit(f"DECIDE #{step}", "Непридатне рішення", "Контролер не повернув JSON route/action, тому можна застосувати структурний fallback.")
            controller_status_notes.append(
                "The controller returned prose or malformed output without a usable route/action, so no new business fact was verified by that controller decision."
            )
        pick = str(decision.get("route") or decision.get("action") or "answer").strip()
        fallback_on = str(meta.get("controller_structural_fallback", "1")).strip().lower() in ("1", "true", "on", "yes")
        if fallback_on:
            fallback_decision, fallback_reason = _fallback_route_decision(text, history, routes)
            invalid_pick = pick not in routes and pick not in ("answer", "")
            if fallback_decision and (not controller_usable or invalid_pick):
                decision = fallback_decision
                pick = str(decision.get("route") or "answer").strip()
                emit(
                    f"DECIDE #{step}",
                    "Аварійний route",
                    f"controller output unusable/unsafe for factual request → {pick} ({fallback_reason})",
                )
        if pick in ("answer", "") or pick not in routes:
            if pick not in ("answer", "") and pick not in routes:
                controller_status_notes.append(
                    f"The controller selected an unavailable route '{pick}', so no new business fact was verified by that route."
                )
            emit(f"DECIDE #{step}", "Рішення: відповідь", f"route/answer = '{pick or 'answer'}'")
            break
        if pick in done:
            emit(f"DECIDE #{step}", "Стоп", f"роут '{pick}' уже використано цього ходу")
            break
        done.add(pick)
        emit(f"DECIDE #{step}", "Обрано роут", pick)
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
            facts.extend(f"[{route['label']} note] {note}" for note in result.get("notes", []))
            facts.extend(f"[{route['label']} missing] {m}" for m in result.get("missing", []))
            if result.get("state"):
                facts.append(
                    f"[{route['label']} state] "
                    + json.dumps(result["state"], ensure_ascii=False)
                )
            if result.get("answer_instruction"):
                facts.append(f"[{route['label']} instruction] {result['answer_instruction']}")

    # 5) ANSWER — persona + chat + cleaned facts only
    ans_sys = persona
    # Marketing module (toggle): merged into the reply prompt only when enabled.
    marketing_on = str(meta.get("marketing_enabled", "")).strip().lower() in ("1", "true", "on", "yes")
    marketing = settings.marketing_rules if marketing_on and settings and settings.marketing_rules else ""
    if marketing:
        ans_sys += "\n\n[MARKETING PROMPT]\n" + marketing
    if facts:
        ans_sys += "\n\n[VERIFIED FACTS (this chat) — use these, do not contradict or re-ask them]\n" + "\n".join(facts)
    if route_results:
        ans_sys += "\n\n[ROUTE RESULTS THIS TURN]\n" + "\n".join(route_results)
    if controller_status_notes:
        ans_sys += (
            "\n\n[PIPELINE STATUS — not business evidence]\n"
            + "\n".join(f"- {note}" for note in controller_status_notes)
            + "\nIf the client is asking to bring/send/repair/buy a concrete item or service and scope/availability for that subject is not already present in VERIFIED FACTS or ROUTE RESULTS, do not give drop-off/contact instructions as acceptance. Also do not claim the item is absent from the catalog merely because the controller failed; say it needs checking or ask the one detail needed to check it."
        )
    biz = meta.get("business_info") if isinstance(meta.get("business_info"), dict) else None
    if biz and _needs_business_contacts(text, route_results, facts):
        biz_lines = "\n".join(f"- {k}: {v}" for k, v in biz.items() if str(v).strip())
        if biz_lines:
            ans_sys += ("\n\n[BUSINESS CONTACTS — the ONLY source for address, hours, phone, payment, "
                        "delivery; use these exact values, never invent. State only the fact the client asked.]\n" + biz_lines)
    ans_sys += "\n\n" + answer_prompt
    amsgs = [{"role": "system", "content": ans_sys}] + _recent(history, text)
    emit("ANSWER", "Вхід у модель", _fmt_msgs(amsgs))
    try:
        temp = float(settings.temperature) if settings and settings.temperature else 0.3
    except (ValueError, TypeError):
        temp = 0.3
    try:
        answer_tokens = min(1200, max(120, int(settings.max_tokens or 700))) if settings else 700
    except (ValueError, TypeError):
        answer_tokens = 700
    raw_answer = await _safe_chat(amsgs, model, base_url, api_key, temp, answer_tokens, retry=True,
                                  emit=emit, label="ANSWER")
    emit("ANSWER", "Сира відповідь моделі", str(raw_answer) or "[порожньо — модель нічого не повернула]")
    safe_fallback = _ua_fallback(settings)
    answer = _clean_answer(raw_answer, fallback=safe_fallback)
    # Hard guard: never send an empty / None / placeholder reply to the client
    # (e.g. when the LLM server is overloaded and returns nothing).
    if not answer or str(answer).strip().lower() in ("none", "null", "undefined", "nil", "-"):
        emit("ANSWER", "Фолбек", f"Модель віддала порожнє/None/«{str(raw_answer)[:40]}» → підставлено запасний текст.")
        answer = safe_fallback

    # Persist the verified facts/steps (deduped, capped) so the next turn's model
    # remembers what it already checked — no re-verifying, no assuming.
    seen, kept = set(), []
    for f in facts:
        key = f.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(f.strip())
    memory["_facts"] = kept[-8:]
    emit("ANSWER", "OK", f"Відповідь: {len(answer)} симв. · памʼять фактів: {len(memory['_facts'])}")
    return answer, memory


def _sanitize_query(q: str) -> str:
    """Guard against degenerate model output (e.g. a token repeated thousands of
    times): keep up to 8 unique keyword tokens, cap length."""
    q = (q or "").strip()
    if not q:
        return ""
    seen, out = set(), []
    for tok in q.replace("\n", " ").split():
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
        if len(out) >= 8:
            break
    return " ".join(out)[:120]


def _route_query_fallback(route: dict, request: dict) -> str:
    """Last-resort source query when a local LLM returns empty JSON.

    This does not decide the answer. It only prevents a verified route call from
    becoming an empty tool call; the route validator still has to confirm facts.
    """
    tool = route.get("tool_name") or ""
    if tool == "get_business_info":
        return str(request.get("question") or request.get("subject") or "").strip()[:120]
    bits = [
        str(request.get("subject") or ""),
        str(request.get("identifier") or ""),
        str(request.get("operation") or ""),
        str(request.get("question") or ""),
    ]
    if tool in {"search_catalog", "search_knowledge", "web_research", "search_parts", "open_url"}:
        return _sanitize_query(" ".join(bits))
    return ""


def _conduct_precheck(text: str) -> bool:
    """Cheap safety net before the LLM conduct route.

    The LLM still owns nuanced tone decisions, but obvious obscene abuse must
    not slip through when a local model returns an empty or malformed answer.
    """
    return bool(_CONDUCT_PRECHECK_RE.search(text or ""))


async def _judge_conduct(text, prompt, model, base_url, api_key):
    """Isolated conduct classifier — judges ONLY the current message (the warning
    count / ban decision belongs to the outer pipeline, not this call)."""
    out = await _safe_chat([{"role": "system", "content": prompt}, {"role": "user", "content": text}],
                           model, base_url, api_key, 0.0, 4, retry=False)
    return "warn" if "warn" in (out or "").lower() else "normal"


async def _safe_chat(messages, model, base_url, api_key, temperature, max_tokens, retry=False,
                     emit=None, label="LLM"):
    err = None
    try:
        out = await chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                         base_url=base_url, api_key=api_key, raise_error=True)
    except Exception as e:
        err, out = e, ""
    if retry and not str(out or "").strip():
        try:
            out = await chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                             base_url=base_url, api_key=api_key, raise_error=True)
        except Exception as e:
            err, out = e, ""
    if err:
        logger.warning(f"lean chat failed (model={model}): {err}")
        # Surface the real reason (wrong model, bad key, rate limit) into the trace
        # instead of a silent empty completion.
        if emit:
            emit(label, "Помилка виклику LLM", f"model={model}\n{type(err).__name__}: {err}")
    return out or ""


async def _run_route_session(route, request, text, tenant_id, db, settings, syn_map,
                             serper_key, model, base_url, api_key, emit, step):
    """Run one route-owned LLM session with short-lived private memory."""
    system = (
        f"[ROUTE SOURCE]\n{route.get('source_description') or ''}\n\n"
        f"[SOURCE CONTENT MAP]\n{route.get('content_map') or 'No content map configured for this source.'}\n\n"
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
    emit(f"QUERY #{step}", "Вхід у модель", _fmt_msgs(route_memory))
    query_raw = await _safe_chat(route_memory, model, base_url, api_key, 0.0, 80, retry=True)
    try:
        query = str(_extract_json(query_raw).get("query") or "").strip()
    except Exception:
        query = ""
    query = _sanitize_query(query)
    if not query:
        query = _route_query_fallback(route, request)
        if query:
            emit(f"QUERY #{step}", "Фолбек запиту", query)
    emit(f"QUERY #{step}", route["code"], query or "[empty query]")

    raw_result, tool = await _run_tool(
        route, query, text, tenant_id, db, settings, syn_map, serper_key,
        requested_fact=str(request.get("requested_fact") or ""),
    )
    emit(f"TOOL #{step}", f"{route['code']} → {tool}", str(raw_result)[:1500])

    route_memory.append({"role": "assistant", "content": query_raw or '{"query":""}'})
    route_memory.append({"role": "user", "content": (
        "PHASE: VALIDATE_SOURCE_RESULT\n"
        f"REQUEST: {json.dumps(request, ensure_ascii=False)}\n"
        f"SOURCE_RESULT:\n{str(raw_result or '')[:5000]}\n"
        'Return JSON only: {"relevant":true|false,"sufficient":true|false,'
        '"match_status":"confirmed|partial|denied|unknown",'
        '"facts":["..."],"notes":["conditions/exclusions/important context"],'
        '"missing":["needed client detail"],"reply_hint":"short guidance for final assistant"|null,'
        '"state":{"topic":"...","selected_item":"...","known_client_data":{},"pending_checks":[],"conditions":[],"exclusions":[]}|null,'
        '"answer_instruction":"what the final chat model should do next"|null,'
        '"fallback":"..."|null}'
    )})
    emit(f"CLEAN #{step}", "Вхід у модель", _fmt_msgs(route_memory))
    out = await _safe_chat(route_memory, model, base_url, api_key, 0.0, 450, retry=True)
    out = (out or "").strip()
    try:
        parsed = _extract_json(out)
        facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
        facts = [str(f).strip() for f in facts if str(f).strip()]
        notes = parsed.get("notes") if isinstance(parsed.get("notes"), list) else []
        notes = [str(f).strip() for f in notes if str(f).strip()]
        missing = parsed.get("missing") if isinstance(parsed.get("missing"), list) else []
        missing = [str(f).strip() for f in missing if str(f).strip()]
        relevant = parsed.get("relevant") is True
        result = {
            "relevant": relevant,
            "sufficient": relevant and parsed.get("sufficient") is True,
            "match_status": str(parsed.get("match_status") or ("confirmed" if relevant else "unknown")),
            "facts": facts if relevant else [],
            "notes": notes if relevant else [],
            "missing": missing if relevant else [],
            "reply_hint": str(parsed.get("reply_hint")).strip() if parsed.get("reply_hint") else None,
            "state": parsed.get("state") if isinstance(parsed.get("state"), dict) else None,
            "answer_instruction": str(parsed.get("answer_instruction")).strip() if parsed.get("answer_instruction") else None,
            "fallback": str(parsed.get("fallback")).strip() if parsed.get("fallback") else None,
        }
    except Exception:
        result = {
            "relevant": False, "sufficient": False, "match_status": "unknown",
            "facts": [], "notes": [], "missing": [], "reply_hint": None,
            "state": None, "answer_instruction": None,
            "fallback": "validation_failed",
        }
    emit(f"CLEAN #{step}", "Очищено", json.dumps(result, ensure_ascii=False))
    return result
