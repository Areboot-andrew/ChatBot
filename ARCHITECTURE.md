# Архітектура універсального чат-агента

> Жива карта системи: що працює, де заглушки, що недороблено.
> Оновлювати при кожній суттєвій зміні ядра.

> Актуально з міграції `d5e6f7a8b9c0`: Lean є основним двигуном. Controller,
> кожен route worker і final answer мають окремий контекст. Сирі результати
> джерел ніколи не передаються у фінальну відповідь.

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
        ├─ engine == "lean"   ──►  run_agent_lean()   app/core/agent_lean.py ← ОСНОВНИЙ
        │
        ├─ engine == "agent"  ──►  run_agent()        app/core/agent.py   ← ВІДКАТ
        │
        └─ engine == "classic" ─►  одно-прохідний роутер (старий, лишений для сумісності)
        │
        ▼
   Відповідь → канал → клієнт
   + історія та пам'ять у Redis
```

Усі канали йдуть через **один** `process_message_pipeline()`. Тест-чат у панелі — теж він, з `trace`-колбеком.

---

## 2. Lean-цикл — `run_agent_lean()`  [app/core/agent_lean.py]

Одна головна модель веде діалог, але не читає сирі бази. Кожен route запускає
власну короткочасну LLM-сесію лише зі своїми трьома промптами.

```
run_agent_lean(text, history, tenant_id, db, settings, trace, memory)
│
├─ CONTROLLER:
│    persona + business_rules + chat + карта routes
│    → {route, question, requested_fact, subject, identifier,
│       operation, qualifiers}
│
├─ LOOP (max = meta.agent_max_iterations, максимум 3):
│    │
│    ├─ route == "answer"? → вихід
│    └─ PRIVATE ROUTE SESSION:
│         source_description + query_prompt + validation_prompt
│         → модель формує source query
│         → інструмент повертає сирий результат у ту саму private memory
│         → модель повертає {relevant, sufficient, facts, fallback}
│         → private memory знищується
│
├─ FINAL ANSWER MODE:
│    persona + business_rules + [MARKETING] + chat
│  + очищені route results + lean_answer_prompt
│    → жива відповідь клієнту
│
└─ У session memory зберігається лише conduct/ban state.
   Route memory і сирі пошуки не переживають поточний виклик.
```

**Запобіжники:**
- кожен роут має рівно три редаговані поля: опис джерела, query і validation;
- повний зміст знайденої фрази звіряється з внутрішнім питанням;
- сирий результат не потрапляє у фінальну модель;
- один route не викликається двічі за той самий хід;
- persona, маркетинг та інші routes не потрапляють у private route memory.

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
Route `search_parts` спершу використовує налаштовані URL-шаблони та домени,
після чого може перейти до загального веб-пошуку.

Діагностика парсера/пошуку — у панелі: **Довідка → Діагностика** (`/admin/api/test-parse`, `/admin/api/test-search`).

---

## 5. Пам'ять і історія  [app/core/history.py]

| Що | Клас | Ключ Redis | TTL |
|---|---|---|---|
| Історія діалогу (останні 20 повідомлень) | `HistoryManager` | `dialog:{channel}:{chat}` | 72 год |
| Пам'ять сесії (conduct/ban state) | `MemoryManager` | `memory:{chat_key}` | 72 год |

`chat_key` по каналах:
- Telegram-бот: `telegram:{channel_id}:{chat_id}`
- Telegram-userbot: `tg_user:{channel_id}:{chat_id}`
- Webchat: `webchat:{channel_id}:{session_id}`

Сирі пошуки, route memory та їхні дампи між повідомленнями не зберігаються.

---

## 6. Конфігурація (все з панелі, нуль хардкоду)

| Налаштування | Де в панелі | Поле в БД |
|---|---|---|
| Персона, тон | Налаштування | bot_settings.system_prompt |
| Бізнес-правила, маркетинг | Налаштування | business_rules / marketing_rules |
| Controller / answer / conduct / warning | Налаштування | meta.lean_*_prompt |
| Режим і ліміт кроків | Налаштування | meta.engine / agent_max_iterations |
| Serper ключ і зовнішні джерела | Налаштування | meta.serper_api_key / parts_sites / price_search_urls |
| Бізнес-факти (адреса/години/оплата) | База знань → Бізнес-факти | meta.business_info |
| Q&A, документи | База знань | qa_pairs / kb_documents (+Qdrant) |
| Прайси | Прайси та Послуги | service_categories / service_prices |
| Routes + 3 власні промпти | База знань → Схема Логіки | knowledge_types.meta |
| Канали (TG бот/userbot/сайт) | Канали | channels |

---

## 7. Статус компонентів

### ✅ Робоче
- Агентний цикл, всі інструменти крім реальної ескалації
- Telegram-бот (webhook авто-реєстрація), Telegram-userbot (генерація сесії з панелі)
- Webchat-віджет (плаваюча кнопка + вбудована форма) з origin-захистом
- Поетапний каталог, таргетовані ціни/QA, веб-пошук з парсингом
- Історія чату та окремий conduct/ban state
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
│   ├── agent_lean.py    ← ОСНОВНЕ ЯДРО (controller, private route sessions, answer)
│   ├── agent.py         ← старий Agent для відкату + спільні tool helpers
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
