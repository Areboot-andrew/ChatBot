"""
Lean isolated-call agent (engine = "lean").

Built to the owner's design: stop dragging one fat 4500-token context through
every model call. Instead, split work into small isolated calls, each with only
its own minimal prompt and only the data it needs:

  1. DECIDE  — persona + a tiny source map + chat + already-cleaned facts.
               One job: pick the next source (or answer). Small, so the local
               model reliably returns JSON.
  2. tool    — the engine runs the actual DB/web fetch (raw text).
  3. CLEAN   — a STATELESS call with NO system prompt: take the raw source text
               and the client's need, return only the cleaned relevant facts.
  4. ANSWER  — persona + chat + the cleaned facts -> the client reply.

Only cleaned facts travel forward in the turn — raw bases never bloat context.
Reuses the existing retrieval functions from agent.py (no duplicated SQL).
"""
import logging

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

# The whole routing instruction — a few lines, not kilometers of per-route essays.
SOURCE_MAP = """Decide the NEXT step. Reply with ONE compact JSON line, nothing else:
{"action":"<source or answer>","query":"<2-4 keywords or empty>"}

Sources (pick the one that holds the fact the client needs):
- catalog        — our repair services and prices. query = device + operation (e.g. "ремонт колонки", "заміна дисплея смартфон").
- knowledge      — our policies / warranty / FAQ. query = subject (e.g. "гарантія").
- business_info  — address, working hours, phone, payment, delivery. query = the field (e.g. "address").
- web            — identify an UNKNOWN device type only. query = name + "device type".
- answer         — reply now: greeting, small talk, off-topic, or you already have the needed facts.

Pick a source only when you genuinely lack a fact. Greetings/chit-chat/off-topic = answer."""

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

    async def _llm(messages, temperature, max_tokens):
        return await chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                          base_url=base_url, api_key=api_key, raise_error=True)

    facts = []          # cleaned, filtered facts only
    done_actions = set()

    # --- isolated DECIDE -> tool -> CLEAN loop ---
    for step in range(1, max_iter + 1):
        sys = persona + "\n\n[ROUTER MODE — you are not talking to the client now]\n" + SOURCE_MAP
        if facts:
            sys += "\n\n[FACTS YOU ALREADY HAVE]\n" + "\n".join(facts)
        decide_msgs = [{"role": "system", "content": sys}] + _recent(history, text)

        try:
            raw = await chat(decide_msgs, model=model, temperature=0.1, max_tokens=120,
                             base_url=base_url, api_key=api_key, raise_error=True)
        except Exception as e:
            emit(f"DECIDE #{step}", "Помилка → відповідь", str(e))
            break
        if not str(raw or "").strip():
            try:
                raw = await chat(decide_msgs, model=model, temperature=0.1, max_tokens=120,
                                 base_url=base_url, api_key=api_key, raise_error=True)
            except Exception:
                raw = ""
        emit(f"DECIDE #{step}", "Сире рішення", str(raw))

        try:
            decision = _extract_json(raw)
        except Exception:
            decision = {"action": "answer", "query": ""}
        action = str(decision.get("action", "answer")).lower().strip()
        query = str(decision.get("query", "") or "").strip()
        emit(f"DECIDE #{step}", "Рішення", f"action={action} query='{query}'")

        if action in ("answer", "") or action not in ("catalog", "knowledge", "business_info", "web", "list_categories"):
            break
        if f"{action}:{query.lower()}" in done_actions:
            break
        done_actions.add(f"{action}:{query.lower()}")

        # --- run the actual source (raw) ---
        if action == "catalog":
            raw_result = await _tool_search_catalog(query or text, tenant_id, db, synonyms=syn_map)
        elif action == "list_categories":
            raw_result = await _tool_list_categories(tenant_id, db)
        elif action == "knowledge":
            raw_result = await _tool_search_knowledge(query or text, tenant_id, db, settings)
        elif action == "business_info":
            raw_result = _tool_get_business_info(query, settings)
        elif action == "web":
            import asyncio
            from app.core.tools import web_research
            raw_result = await asyncio.to_thread(web_research, query or text, 3, 3000, serper_key)
        else:
            raw_result = ""
        emit(f"TOOL #{step}", action, str(raw_result)[:1500])

        # --- isolated STATELESS clean (no system prompt) ---
        cleaned = await _clean_source(query or text, action, raw_result, model, base_url, api_key, emit, step)
        if cleaned:
            facts.append(f"[{action}] {cleaned}")

    # --- isolated ANSWER ---
    ans_sys = persona
    if facts:
        ans_sys += ("\n\n[VERIFIED FACTS — answer only from these and the client's own words. "
                    "Never invent an address, price, phone or schedule that is not here.]\n" + "\n".join(facts))
    ans_msgs = [{"role": "system", "content": ans_sys}] + _recent(history, text)
    try:
        temp = float(settings.temperature) if settings and settings.temperature else 0.3
    except (ValueError, TypeError):
        temp = 0.3
    try:
        answer = await chat(ans_msgs, model=model, temperature=temp, max_tokens=700,
                            base_url=base_url, api_key=api_key, raise_error=True)
    except Exception as e:
        emit("ANSWER", "Помилка", str(e))
        answer = settings.fallback_text if settings and settings.fallback_text else "Технічна заминка, спробуйте ще раз."
    answer = _clean_answer(answer, fallback=(settings.fallback_text if settings else "") or "")
    emit("ANSWER", "OK", f"Фактів: {len(facts)}")
    return answer, memory


async def _clean_source(need, source, raw, model, base_url, api_key, emit, step):
    """STATELESS cleaner — no persona, no rules. Just filter raw -> clean facts."""
    if _is_empty(raw):
        emit(f"CLEAN #{step}", "Порожньо", "Джерело без корисних даних")
        return ""
    user = (
        f"Клієнту потрібно: {need}\n"
        f"Джерело: {source}\n"
        f"Сирі дані джерела:\n{str(raw)[:3500]}\n\n"
        "Витягни ТІЛЬКИ факти, релевантні до потреби клієнта. Короткі рядки, українською, "
        "без коментарів, без вигаданих значень. Якщо релевантного нема — поверни рівно: -"
    )
    try:
        out = await chat([{"role": "user", "content": user}], model=model, temperature=0,
                         max_tokens=350, base_url=base_url, api_key=api_key, raise_error=True)
    except Exception as e:
        emit(f"CLEAN #{step}", "Помилка", str(e))
        return ""
    out = (out or "").strip()
    emit(f"CLEAN #{step}", "Очищено", out or "-")
    return "" if out in ("-", "") else out
