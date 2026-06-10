# SPEC: chat-gateway — технічна архітектура та TODO

> Формат: внутрішня специфікація для розробки (Claude Code). Мова коду: Python 3.12.
> Все в Docker, деплой Coolify, внутрішня мережа. LM Studio — зовнішня залежність (окрема VM).

---

## 0. Сервіси (docker-compose)

| Сервіс | Образ/код | Порт | Призначення |
|---|---|---|---|
| `gateway` | свій (FastAPI) | 8000 | API каналів, RAG-пайплайн, роутер до LLM |
| `admin` | свій (FastAPI + Jinja2/HTMX, або React build у тому ж контейнері) | 8000 `/admin` | веб-адмінка (можна в одному контейнері з gateway) |
| `worker` | свій (той самий образ, інша команда) | — | черга LLM-запитів, інжест файлів, скрейпер, крони |
| `postgres` | postgres:16 | 5432 | конфіги, канали, ключі, прайси, логи діалогів |
| `qdrant` | qdrant/qdrant | 6333 | вектори RAG |
| `redis` | redis:7 | 6379 | черга (arq/RQ), сесії, історія діалогів, rate limit |
| LM Studio | ЗОВНІ, VM | 1234 | /v1/chat/completions, /v1/embeddings |

Один репозиторій, один Docker-образ для gateway/admin/worker (різні entrypoint):
```
chat-gateway/
├── app/
│   ├── main.py            # FastAPI: канали + admin router
│   ├── config.py          # pydantic-settings, env
│   ├── channels/          # telegram.py, viber.py, webchat.py
│   ├── core/              # llm.py, rag.py, intents.py, prices.py, history.py
│   ├── admin/             # routers + templates (HTMX)
│   ├── ingest/            # parsers.py, scraper.py, chunker.py, embedder.py
│   ├── models/            # SQLAlchemy
│   └── workers/           # arq tasks
├── widget/                # JS-віджет сайту (vanilla, 1 файл, збірка esbuild)
├── docker-compose.yml
├── Dockerfile
└── alembic/               # міграції
```

**Стек і мови:**
- Backend: Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, arq (черга на Redis), httpx.
- LLM-клієнт: `openai` SDK, base_url = `http://LMSTUDIO_IP:1234/v1`.
- Admin UI: Jinja2 + HTMX + Tailwind (без окремого фронт-стека — швидше і простіше підтримувати). Якщо захочеться SPA — React, але v1 = HTMX.
- Віджет сайту: vanilla JS (~300 рядків), WebSocket до gateway.
- Парсери: pdfplumber, python-docx, openpyxl/pandas, BeautifulSoup4, trafilatura (чистка HTML).

---

## 1. Модель даних (Postgres) — хто чий

Центральна ідея: **все прив'язане до tenant → channel**. Tenant = бізнес (сервісний центр). Каналів багато: N телеграм-ботів, N вайбер-ботів, N сайтів. Модель завжди знає, від імені кого відповідає.

```sql
-- бізнеси (на виріст; v1 може мати один tenant)
tenants(id uuid pk, name, created_at)

-- канали: КОЖЕН телеграм-бот / вайбер-бот / сайт = рядок
channels(
  id uuid pk,
  tenant_id fk,
  type enum('telegram','viber','webchat'),
  name text,                    -- "ТГ основний", "Сайт repair.lviv.ua"
  enabled bool,
  credentials jsonb,            -- {bot_token} | {viber_token} | {allowed_origins:[...]}
  webhook_secret text,          -- перевірка вхідних webhook
  persona_override text,        -- доповнення до системного промпта саме для цього каналу
  greeting text,
  created_at, updated_at
)

-- API-ключі, які видає адмінка (для сайтів і зовнішніх інтеграцій)
api_keys(
  id uuid pk, tenant_id fk, channel_id fk null,
  key_hash text,                -- зберігаємо ТІЛЬКИ hash (sha256), показуємо ключ один раз
  scopes text[],                -- ['chat'], ['ingest'], ['admin:read']
  expires_at, revoked bool, created_at
)

-- налаштування поведінки (редагується в адмінці, кеш у Redis)
bot_settings(
  tenant_id fk,
  system_prompt text,           -- базовий тон/правила
  escalation_prompt text,
  fallback_text text,           -- "уточню у майстра..."
  llm_model text,               -- ім'я моделі в LM Studio
  temperature float, max_tokens int,
  rag_top_k int default 5, rag_score_threshold float default 0.5
)

-- документи бази знань
kb_documents(
  id uuid pk, tenant_id fk,
  title, category enum('faq','warranty','services','delivery','promo','other'),
  source enum('upload','site'),
  filename, mime, sha256,       -- дедуплікація
  status enum('pending','indexed','failed'),
  chunks_count int, updated_at
)

-- прайси (структуровані, НЕ в RAG)
price_list(
  id serial pk, tenant_id fk,
  device_type, brand, model, model_normalized,  -- lowercase, без пробілів — для LIKE
  service, price_min int, price_max int, duration, note, updated_at
)

-- діалоги і повідомлення (аналітика + handoff)
conversations(id uuid pk, tenant_id, channel_id fk, external_chat_id text,
              status enum('bot','operator','closed'), started_at)
messages(id, conversation_id fk, role enum('user','assistant','operator'),
         content text, meta jsonb,   -- intent, rag_doc_ids, latency_ms, tokens
         created_at)

operators(id, tenant_id, name, tg_chat_id)  -- куди ескалювати
```

**Redis-ключі:**
```
dialog:{channel_id}:{chat_id}   → список останніх 20 повідомлень, TTL 72h
settings:{tenant_id}            → кеш bot_settings (інвалідація при збереженні в адмінці)
queue: arq default              → LLM-задачі (конкурентність = 1!)
ratelimit:{channel_id}:{chat_id}
```

**Qdrant:** одна колекція `kb`, payload = `{tenant_id, doc_id, category, title, lang, updated_at}`. Фільтри пошуку завжди включають `tenant_id` (+ `category`, якщо intent її визначив). Видалення документа = delete by filter `doc_id`.

---

## 2. API-контракти

### 2.1 Вхідні (gateway)
```
POST /tg/{channel_id}                # Telegram webhook (перевірка secret_token)
POST /viber/{channel_id}             # Viber webhook (перевірка підпису X-Viber-Content-Signature)
WS   /ws/chat                        # віджет сайту; auth: api_key + origin check
POST /api/v1/chat                    # універсальний REST (зовнішні інтеграції за api_key)
     body: {chat_id, text, image_b64?}
GET  /health                         # для Coolify
```

### 2.2 Адмінка (`/admin`, сесійна auth + IP allowlist)
```
CRUD /admin/channels                 # додати ТГ-токен → авто-реєстрація webhook через Bot API
CRUD /admin/api-keys                 # генерація ключа (показ 1 раз), revoke
GET/PUT /admin/settings              # системний промпт, тон, llm-параметри, пороги RAG
POST /admin/kb/upload                # multipart файли → задача інжесту в worker
GET/DELETE /admin/kb/documents
POST /admin/kb/reindex-site          # запуск скрейпера вручну
POST /admin/prices/upload            # xlsx → валідація → preview → commit
GET  /admin/prices                   # таблиця з пошуком
GET  /admin/conversations            # логи, фільтр по каналу; кнопка "перехопити"
POST /admin/test-chat                # пісочниця: тест промпта без реальних каналів
```

### 2.3 Вихідні (gateway → залежності)
```
LM Studio: POST /v1/chat/completions   (messages, images у content як base64)
           POST /v1/embeddings         (embedding-модель: bge-m3 / multilingual-e5)
Qdrant:    /collections/kb/points/search  (filter: tenant_id, category?)
Telegram:  sendMessage, sendChatAction('typing'), getFile (фото від клієнта)
Viber:     /pa/send_message
```

---

## 3. Потік обробки повідомлення (ядро)

```
webhook/ws → нормалізація у InboundMessage{channel_id, chat_id, text, images[]}
  1. channel = load(channel_id); якщо disabled → ігнор
  2. rate limit (Redis), antiflood
  3. історія = Redis dialog:{channel_id}:{chat_id}
  4. intent = classify(text)        # rules-first (regex ціна/гарантія/статус) → LLM-fallback (JSON)
  5. контекст:
     - intent=price  → SQL price_list (екстракція brand/model/service промптом → WHERE model_normalized LIKE ...)
                       → у контекст ТІЛЬКИ знайдені 1–3 рядки; >3 збігів → уточнююче питання
     - інакше        → Qdrant search(tenant_id, category?, top_k, threshold)
                       → нічого вище порога → fallback_text + ескалація
  6. промпт = bot_settings.system_prompt
            + channels.persona_override          # ← модель ЗНАЄ, для якого каналу/сайту пише
            + "Канал: {channel.name}. Ти асистент сервісного центру {tenant.name}..."
            + контекст (RAG-чанки АБО рядки прайса)
            + історія + повідомлення (+ images для vision)
  7. задача в чергу arq (конкурентність 1, бо локальна модель) → typing-індикатор
  8. відповідь LLM → strip <think>…</think> → ліміт довжини → markdown під канал
  9. send через адаптер каналу; запис у messages (intent, doc_ids, latency)
 10. тригери ескалації: 2× fallback поспіль | негатив | пряме прохання людини
     → status='operator', сповіщення в tg оператора з останніми 10 повідомленнями,
       бот у цій розмові замовкає до 'closed'
```

**Правило чесності:** на пряме питання «ти бот?» — чесна відповідь («цифровий асистент сервісу, можу покликати майстра»). Природний тон — так; пряма брехня — ні (репутаційний і регуляторний ризик).

---

## 4. Інжест знань (worker)

```
upload → sha256 (дубль? skip) → parser за mime:
  pdf → pdfplumber | docx → python-docx | md/txt → as-is
  xlsx/csv: якщо шаблон прайса → price_list; інакше → текстові рядки в RAG
→ chunker: 400–600 ток., overlap 60, title префіксом у кожен чанк
→ embeddings батчами через LM Studio
→ upsert у Qdrant (payload з category з форми завантаження)
→ kb_documents.status='indexed'

scraper (крон 1×/добу + кнопка): sitemap.xml → trafilatura → диф по hash сторінки →
  переіндексація лише змінених; category='services'/'faq' за url-правилами з адмінки
```

---

## 5. Безпека

- Секрети тільки в env (Coolify), у БД — лише hash api-ключів; credentials каналів у jsonb — шифрування app-рівня (Fernet, ключ в env).
- Webhook: Telegram `secret_token`, Viber перевірка HMAC-підпису.
- Адмінка: логін/пароль (argon2), сесії, IP allowlist внутрішньої мережі, CSRF.
- Віджет: api_key (scope=chat) + CORS allowed_origins з channels.credentials.
- LM Studio порт 1234 — закритий фаєрволом від усього, крім хоста gateway.
- PII у логах: телефони/адреси клієнтів не маскуємо у v1, але доступ до /admin/conversations — тільки адмін; backlog: маскування.

---

## 6. TODO по етапах (порядок виконання в Claude Code)

### M1 — скелет (день 1–3)
- [ ] репо, Dockerfile (multi-stage), docker-compose (всі 5 сервісів), alembic init
- [ ] config.py (env: LMSTUDIO_URL, DB, REDIS, SECRET_KEY, FERNET_KEY)
- [ ] моделі SQLAlchemy з §1 + перша міграція
- [ ] core/llm.py: chat(), embed(), strip_think(); ретраї, таймаут 60s, фолбек-текст при недоступності
- [ ] core/history.py (Redis), arq worker з конкурентністю 1
- [ ] /health; деплой у Coolify, smoke-test через /api/v1/chat

### M2 — адмінка-мінімум (день 4–8)
- [ ] auth (сесії, argon2), layout HTMX
- [ ] CRUD channels: додавання tg-токена → виклик setWebhook автоматично; viber; webchat(origins)
- [ ] settings: промпт/тон/температура/пороги; кеш у Redis + інвалідація
- [ ] api-keys: generate(показ 1 раз)/revoke
- [ ] test-chat пісочниця

### M3 — RAG (день 9–13)
- [ ] ingest: parsers, chunker, embedder, qdrant client; статуси документів
- [ ] /admin/kb: upload з категорією, список, видалення (+ delete vectors)
- [ ] core/rag.py: search з фільтрами tenant+category, threshold
- [ ] scraper + крон + кнопка reindex
- [ ] інтеграція в потік §3 (крок 5b)

### M4 — прайси (день 14–17)
- [ ] price_list + завантаження xlsx з preview перед commit
- [ ] core/intents.py: rules-first класифікатор; екстракція {brand,model,service} у JSON
- [ ] core/prices.py: нормалізація моделі, пошук, логіка уточнення при кількох збігах
- [ ] інтеграція в потік (крок 5a) + тест: «скільки коштує екран на айфон 13»

### M5 — канали (день 18–24)
- [ ] webchat: WS endpoint + widget/ (typing, історія сесії, embed-сніпет генерує адмінка)
- [ ] telegram: webhook /tg/{id}, фото→base64→vision, typing, мульти-боти з одної таблиці
- [ ] viber: webhook, підпис; ⚠️ перевірити актуальну платну модель Viber-ботів для UA
- [ ] e2e: одне питання у 3 каналах → консистентні відповіді, ізольована історія

### M6 — ескалація + експлуатація (день 25–28)
- [ ] handoff: тригери, сповіщення оператору, режим operator/closed, перехоплення з адмінки
- [ ] /admin/conversations: перегляд, фільтри, метрики (топ-інтенти, % fallback)
- [ ] моніторинг: алерт у tg якщо LM Studio down; volumes/бекапи; rate limits
- [ ] прогін 30–50 реальних питань → правки промпта/KB → реліз

---

## 7. Env-змінні (мінімум)
```
LMSTUDIO_URL=http://10.0.0.5:1234/v1
LLM_MODEL=gemma-3-27b-it          EMBED_MODEL=bge-m3
DATABASE_URL=postgresql+psycopg://...
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
SECRET_KEY=...   FERNET_KEY=...
ADMIN_DEFAULT_PASSWORD=...        PUBLIC_BASE_URL=https://bot.example.com
```

---

## 8. Публікація: публічний домен, бекенд усередині

**Принцип:** назовні — тільки HTTP-шар через Traefik (Coolify), усі сховища і LLM — лише внутрішня мережа.

### 8.1 Маршрутизація (Traefik labels у compose)
```
admin.example.ua          → gateway:8000 /admin/*     # адмінка = повноцінний публічний сайт
bot.example.ua/tg/*       → gateway:8000              # Telegram webhooks
bot.example.ua/viber/*    → gateway:8000              # Viber webhooks
bot.example.ua/ws/chat    → gateway:8000              # WebSocket віджета
bot.example.ua/api/v1/*   → gateway:8000              # REST для сайтів/інтеграцій (api_key)
bot.example.ua/widget.js  → gateway:8000 static       # embed-скрипт для чужих сайтів
```
Все інше — `expose` тільки у docker-мережі, ЖОДНИХ `ports:` для postgres/redis/qdrant.
LM Studio :1234 — ufw allow тільки з IP хоста gateway.

### 8.2 Безпека публічної адмінки (обов'язково, бо інтернет)
- [ ] Login: argon2 + **2FA TOTP** (pyotp + QR при першому вході)
- [ ] Rate limit на /admin/login (5 спроб → lock 15 хв, лог IP)
- [ ] Сесії: Secure, HttpOnly, SameSite=Lax, TTL 12h, ротація після логіна
- [ ] CSRF-токени на всі POST; security headers (CSP, X-Frame-Options=DENY, HSTS)
- [ ] Audit log: хто/коли змінив токени, ключі, промпт (таблиця admin_audit)
- [ ] Опційно: Cloudflare proxy перед доменом (ховає origin IP, WAF, бот-фільтр)
- [ ] Окремі ролі на виріст: owner / operator (operator бачить діалоги, не бачить ключі)

### 8.3 Віджет для зовнішніх сайтів
Адмінка генерує сніпет на канал:
```html
<script src="https://bot.example.ua/widget.js"
        data-key="pk_live_xxx" defer></script>
```
- `data-key` = публічний api_key каналу (scope=chat), валідний лише з Origin зі списку allowed_origins цього каналу
- WS handshake: перевірка Origin + key → видача короткоживучого session-токена
- Жодних секретів у фронтенді; rate limit per session

### 8.4 Сповіщення — push, не polling
- Telegram/Viber → webhook (миттєво); сайт → WebSocket (миттєво)
- Ескалація оператору → push у tg-чат оператора в момент тригера
- Крони worker: скрейпер сайту 1×/добу; health-check LM Studio кожні 30–60 с → алерт у tg при падінні; backup dump postgres 1×/добу на volume

---

## 9. Типи знань і керування ними в адмінці

**Принцип:** 4 типи даних → 4 сховища → 4 інтерфейси керування. Роутер намірів вирішує, де шукати. У вектори йде ТІЛЬКИ неструктурований текст.

| Тип | Приклад | Сховище | Керування в адмінці | Пошук |
|---|---|---|---|---|
| Q&A-пари | "мікрохвильовки берете?" → "ні, ремонтуємо..." | таблиця qa_pairs + вектор ПИТАННЯ в Qdrant | редактор пар (без файлів): додати/змінити/вимкнути, кілька формулювань питання на одну відповідь | векторний по питанню, high threshold |
| Вільний текст | гарантія, умови, описи послуг | kb_documents + чанки в Qdrant | upload файлів АБО текстовий редактор; категорія | векторний по чанках |
| Прайси + характеристики | ціна, тип матриці, оригінал/копія | price_list + specs jsonb | xlsx-імпорт з preview; інлайн-редагування рядка | SQL за brand/model/service |
| Товари + сумісність | "батарея Note 11 підійде до 11S?" | products + product_compat | xlsx-імпорт (колонка сумісних моделей через кому) + картка товару | SQL точний join |

### 9.1 Додаткові таблиці
```sql
qa_pairs(
  id uuid pk, tenant_id fk,
  question text,                -- основне формулювання
  question_variants text[],     -- альтернативні формулювання (суржик, сленг)
  answer text,
  category text, enabled bool, updated_at
)
-- у Qdrant: окрема колекція qa; point на КОЖНЕ формулювання питання,
-- payload {qa_id, tenant_id}; embedding від питання, у відповідь беремо answer з Postgres

products(
  id pk, tenant_id, sku, name, category,        -- "Дисплей iPhone 13 (оригінал)"
  brand, model_normalized, specs jsonb,
  price int, in_stock bool, updated_at
)
product_compat(product_id fk, compatible_model_normalized text)
-- xlsx-колонка "сумісність": "iPhone 13; iPhone 13 Pro" → розгортається в рядки
```

### 9.2 Роутер намірів (порядок перевірки)
```
1. price        (regex: скільки|ціна|вартість|почому + SQL price_list)
2. compatibility (підійде|сумісн|замість|від іншої моделі → SQL products+compat)
3. qa           (векторний пошук у колекції qa, threshold 0.75 — high, бо пари точні)
4. kb           (векторний пошук у kb-чанках, threshold 0.5)
5. fallback     (нічого → fallback_text + лічильник ескалації)
Кроки 3-4 можна виконувати паралельно і брати найкращий score.
```

### 9.3 Адмінка: вкладка "Знання" = 4 під-вкладки
- **Q&A**: таблиця пар, кнопка "+ пара", поле варіантів питання, тогл on/off,
  лічильник "скільки разів спрацювала" (з messages.meta) — видно мертві пари
- **Схема Логіки (Інтенти)**: візуальний редактор пайплайну бота (Intent Router → SQL → Qdrant → Fallback). Дозволяє гнучко керувати тим, які обробники викликати для конкретних ключових слів.
- **Документи**: як у §4 (upload/текст, категорії, статуси індексації)
- **Прайси**: керування категоріями та цінами. Заповнюється автоматично при старті бази з `knowledge_template.yaml`.

### 9.4 Дебаг Панель (Тест-Чат)
Сторінка `/admin/test-chat` містить:
1. Звичайний інтерфейс чату з ботом.
2. **Консоль розробника (Terminal / LLM Trace)**: візуалізує кожен крок проходження запиту (Який інтент розпізнано → Що знайдено в SQL або CRM мікросервісі → Скільки фактів з Qdrant → Успіх/Помилка LLM генерації). Дозволяє прозоро тестувати RAG та API-відповіді мікросервісів без читання логів контейнера.

### 9.5 TODO (доповнення до M3/M4)
- [ ] M3+: колекція qa в Qdrant, CRUD qa_pairs, ембедінг variants, роутер крок 3
- [ ] M4+: products/product_compat, xlsx-парсер сумісності, intent compatibility,
      нормалізація моделей спільна з price_list (одна функція normalize_model())
- [ ] статистика спрацювань по кожному типу знань у /admin/conversations

### 9.5 Чистота контексту і двошаровий фолбек (правила ядра)
**Лінива збірка контексту:**
- базовий запит = system_prompt + persona каналу + історія; БЕЗ знань за замовчуванням
- знання (RAG/SQL) додаються ТІЛЬКИ коли роутер визначив намір; топ-N вище порога
- контекст не персистентний: чанки попереднього питання НЕ переносяться в наступне
- ліміт бюджету: ≤ ~2000 токенів знань на запит (top_k обрізається за бюджетом)

**Роутер, доповнений крок 0 і фолбеки:**
```
0. smalltalk (привітання/подяка/офтоп; легка LLM-класифікація або правила)
   → відповідь БЕЗ знань, лише тон. Це фолбек чату.
1-4. price / compatibility / qa / kb — як у §9.2
5. knowledge-fallback: намір предметний, але score < threshold у всіх сховищах
   → fallback_text + escalation_counter++ ; НЕ генерувати відповідь з голови
```
**Жорстке правило в system_prompt:** "Про послуги, ціни, терміни, сумісність відповідай ЛИШЕ з наданого контексту. Немає в контексті — використай фразу ескалації. Не вигадуй."
- [ ] тест: "дякую, гарного дня" → smalltalk, без RAG, без ескалації
- [ ] тест: "скільки коштує ремонт материнки MSI" без даних → ескалація, БЕЗ вигаданої ціни
- [ ] лог у messages.meta: route, scores, context_tokens — для дебагу засмічення
