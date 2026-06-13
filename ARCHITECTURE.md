# Архітектура чат-агента (texno.plus gateway)

> Жива карта системи: що працює, де заглушки, що недороблено.
> Оновлювати при кожній суттєвій зміні ядра.

## 1. Загальний потік повідомлення

```
Клієнт (Telegram-бот / Telegram-userbot / Webchat на сайті)
        │
        ▼
Канал-адаптер  (app/channels/*)
  • telegram.py            handle_telegram_update()
  • telegram_userbot.py    UserbotManager._handle_message()
  • webchat.py             webchat_message()
        │  text, history(Redis), tenant_id, chat_key
        ▼
process_message_pipeline()            app/core/pipeline.py
        │
        ├─ engine == "agent"  ──►  run_agent()        app/core/agent.py   ← ОСНОВНИЙ
        │                          (fallback на classic при помилці)
        │
        └─ engine == "classic" ─►  одно-прохідний роутер (старий, лишений для сумісності)
        │
        ▼
   Відповідь → канал → клієнт
   + історія та пам'ять у Redis
```

Усі канали йдуть через **один** `process_message_pipeline()`. Тест-чат у панелі — теж він, з `trace`-колбеком.

---

## 2. Агентний цикл (ядро) — `run_agent()`  [app/core/agent.py]

Двосходинкова логіка «старого Гіві», generic під multi-tenant.

```
run_agent(text, history, tenant_id, db, settings, trace, memory)
│
├─ Збірка системного промпта:
│    persona (settings.system_prompt)
│  + [BUSINESS RULES] (settings.business_rules)        ← завжди
│  + [CHAT MEMORY] (memory, видимі ключі)
│  + [ALREADY CHECKED THIS CHAT] (memory["_facts"])    ← пам'ять чату
│  + [GATHERED FACTS] (зібране цього ходу)
│  + ROUTER_PROTOCOL + [TENANT ROUTING HINTS] (зі Схеми Логіки)
│
├─ LOOP (max = meta.agent_max_iterations, дефолт 5):
│    │
│    ├─ chat() → JSON рішення:
│    │     {"action", "query", "reason", "memory_patch"}
│    │
│    ├─ memory_patch → оновлює memory (модель сама пише факти чату)
│    │
│    ├─ action == "answer"?
│    │     ├─ так + предметне питання + ще нема фактів
│    │     │     → GUARD: форсований waterfall
│    │     │       _tool_search_catalog → _tool_search_knowledge → _do_web_research
│    │     │       (поки не пересвідчиться; прайс ≠ доказ що НЕ робимо)
│    │     │     → continue (вирішує знову з фактами)
│    │     └─ інакше → вихід з циклу
│    │
│    └─ виконання інструменту (див. §3), результат → gathered[]
│
├─ FINAL ANSWER MODE:
│    persona + business_rules + [MARKETING] + контекст
│  + tpl_evaluation_rules (анти-галюцинації, з панелі)
│  + escalation_prompt (з панелі)
│  + ANSWER_PROTOCOL
│    → chat() → жива відповідь клієнту
│
└─ Зберегти знахідки ходу в memory["_facts"] (макс 12, dedupe)
   return (answer, memory)
```

**Запобіжники (для локальної Gemma):**
- `_looks_substantive()` — відрізняє предметне питання від привітання.
- GUARD waterfall — не дає відповісти з памʼяті без перевірки джерел.
- `_is_empty()` — оцінює, чи інструмент дав корисний факт.
- Повтор інструмента дозволений лише з **іншим** query (drill-down), однаковий блокується.

---

## 3. Інструменти агента (дії)

| Дія | Функція | Джерело | Статус |
|---|---|---|---|
| `list_categories` | `_tool_list_categories()` | SQL ServiceCategory + count | ✅ робоче |
| `search_catalog` | `_tool_search_catalog()` | SQL ServicePrice (поетапно: назва→категорія→список) | ✅ робоче |
| `search_knowledge` | `_tool_search_knowledge()` | SQL QaPair (ILIKE) + Qdrant RAG | ✅ робоче¹ |
| `web_research` | `_do_web_research()`→`web_research()` | DuckDuckGo / Serper + парсинг сторінок | ✅ робоче |
| `open_url` | `fetch_and_parse_url()` | trafilatura + httpx | ✅ робоче |
| `get_business_info` | `_tool_get_business_info()` | settings.meta.business_info | ✅ робоче |
| `escalate` | inline | meta.tpl_escalate_instruction | ⚠️ частково² |

¹ RAG потребує embed-моделі (bge-m3) у LM Studio; без неї повертає 0 чанків.
² `escalate` лише вставляє інструкцію в промпт. **Реального сповіщення оператору ще немає** (немає таблиці operators / надсилання в TG оператора). Заглушка.

---

## 4. Веб-пошук і парсинг  [app/core/tools.py]

```
web_research(query, max_pages, page_chars, serper_key)
│
├─ serper_key заданий? → _serper_search()  (Google + answerBox)
│                  інакше → _ddg_search()  (DuckDuckGo HTML, скоринг лінків)
│
└─ для топ-N лінків → fetch_and_parse_url() → повний текст сторінки
   (не сніпети! реальний захід на сайт)
```
`_do_web_research()` в агенті спершу пробує **Довірені сайти** (meta.fallback_sites, напр. texno.plus), потім відкритий інтернет.

Діагностика парсера/пошуку — у панелі: **Довідка → Діагностика** (`/admin/api/test-parse`, `/admin/api/test-search`).

---

## 5. Пам'ять і історія  [app/core/history.py]

| Що | Клас | Ключ Redis | TTL |
|---|---|---|---|
| Історія діалогу (останні 20 повідомлень) | `HistoryManager` | `dialog:{channel}:{chat}` | 72 год |
| Пам'ять агента (факти чату + memory_patch) | `MemoryManager` | `memory:{chat_key}` | 72 год |

`chat_key` по каналах:
- Telegram-бот: `telegram:{channel_id}:{chat_id}`
- Telegram-userbot: `tg_user:{channel_id}:{chat_id}`
- Webchat: `webchat:{channel_id}:{session_id}`

`memory["_facts"]` = службовий список вже виконаних пошуків чату → інжектиться як
`[ALREADY CHECKED THIS CHAT]`, щоб агент не повторювався і робив фолбек на знайдене.

---

## 6. Конфігурація (все з панелі, нуль хардкоду)

| Налаштування | Де в панелі | Поле в БД |
|---|---|---|
| Персона, тон | Налаштування | bot_settings.system_prompt |
| Бізнес-правила, маркетинг | Налаштування | business_rules / marketing_rules |
| Анти-галюцинації, ескалація | Налаштування | meta.tpl_evaluation_rules / escalation_prompt |
| Режим (agent/classic), кроки, інструменти | Налаштування | meta.engine / agent_max_iterations / enabled_tools |
| Serper ключ, Довірені сайти | Налаштування | meta.serper_api_key / fallback_sites |
| Бізнес-факти (адреса/години/оплата) | База знань → Бізнес-факти | meta.business_info |
| Q&A, документи | База знань | qa_pairs / kb_documents (+Qdrant) |
| Прайси | Прайси та Послуги | service_categories / service_prices |
| Інтенти + reasoning-шаблони зі слотом | База знань → Схема Логіки | knowledge_types (+ meta.reasoning) |
| Канали (TG бот/userbot/сайт) | Канали | channels |

---

## 7. Статус компонентів

### ✅ Робоче
- Агентний цикл, всі інструменти крім реальної ескалації
- Telegram-бот (webhook авто-реєстрація), Telegram-userbot (генерація сесії з панелі)
- Webchat-віджет (плаваюча кнопка + вбудована форма) з origin-захистом
- Поетапний каталог, таргетовані ціни/QA, веб-пошук з парсингом
- Пам'ять чату (історія + факти + memory_patch)
- Прайси: шаблон/експорт/імпорт XLSX-CSV з мапінгом
- Тест-чат з живим трейсом реального пайплайну
- Довідка + діагностика парсера в панелі

### ⚠️ Частково / заглушки
- **escalate** — лише інструктує модель; немає реального сповіщення оператору
  (немає operators-таблиці, TG-сповіщення, режиму operator/closed) — spec §3 кроки 9-10
- **Viber** — тип каналу згаданий у спеці, не реалізований
- **conversations/messages логування** — таблиці зі спеки не пишуться (немає аналітики, топ-інтентів, % fallback)
- **classic engine** — лишений для сумісності, не основний шлях

### ❌ Не реалізовано (зі спеки, backlog)
- 2FA / CSRF / audit log для адмінки (spec §8.2)
- products / product_compat (товари + сумісність)
- Скрейпер сайту по крону (періодична переіндексація)
- Rate limiting, моніторинг LM Studio з алертом

---

## 8. Ключові файли

```
chat-gateway/app/
├── core/
│   ├── agent.py         ← АГЕНТНЕ ЯДРО (run_agent, інструменти, GUARD)
│   ├── pipeline.py      ← вхідна точка, перемикач engine
│   ├── tools.py         ← web_research, fetch_and_parse_url
│   ├── rag.py           ← Qdrant: vectorize + search_knowledge
│   ├── llm.py           ← chat / chat_stream / embed (OpenAI SDK → LM Studio)
│   ├── history.py       ← HistoryManager + MemoryManager (Redis)
│   ├── intents.py       ← detect_intent (classic engine)
│   └── prompt_builder.py← build_system_prompt (classic engine)
├── channels/
│   ├── telegram.py
│   ├── telegram_userbot.py
│   └── webchat.py
├── admin/
│   ├── routes.py        ← вся панель + test-chat + діагностика
│   └── templates/
└── models/              ← SQLAlchemy: tenant, channel, services, knowledge...
```
