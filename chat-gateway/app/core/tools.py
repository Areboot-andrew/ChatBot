import logging

logger = logging.getLogger(__name__)

def search_internet(query: str, max_results: int = 3) -> str:
    """
    Performs a web search using DuckDuckGo HTML Lite and returns a formatted string of the best snippets.
    """
    import httpx
    import urllib.parse
    import re
    logger.info(f"Searching internet for: {query}")
    try:
        data = urllib.parse.urlencode({'q': query})
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = client.post("https://html.duckduckgo.com/html/", content=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp.raise_for_status()
            
            html = resp.text
            snippets_raw = re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
            urls_raw = re.findall(r'class="result__url"[^>]*href="([^"]+)"', html, re.IGNORECASE)
            
            if not snippets_raw:
                return "No search results found."
                
            snippets = []
            for i in range(min(max_results, len(snippets_raw))):
                text = re.sub(r'<[^>]+>', '', snippets_raw[i]).strip()
                text = text.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                url = urls_raw[i] if i < len(urls_raw) else ""
                snippets.append(f"Джерело {i+1}: {url}\nФрагмент: {text}")
                
            result_text = "\n\n".join(snippets)
            
            # Fetch the top 2 pages for deep context
            if urls_raw:
                valid_fetches = 0
                for url in urls_raw[:2]:
                    try:
                        deep_text = fetch_and_parse_url(url)
                        if deep_text and "Error fetching URL" not in deep_text and "Could not extract" not in deep_text:
                            result_text += f"\n\n--- ДОДАТКОВИЙ ПАРСИНГ ({url}) ---\n"
                            # Limit to 2000 chars to save tokens and avoid prompt bloat
                            result_text += deep_text[:2000]
                            valid_fetches += 1
                    except Exception as ex:
                        logger.warning(f"Could not deep parse URL {url}: {ex}")
                    
            return result_text
    except Exception as e:
        logger.error(f"Error during web search: {e}")
        return f"Search error: {e}"

def _serper_search(query: str, serper_key: str) -> tuple:
    """Google search via Serper.dev. Returns (candidates, answer_box_text)."""
    import httpx
    candidates = []
    answer_text = ""
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": 10},
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    box = data.get("answerBox") or {}
    if box:
        answer_text = box.get("answer") or box.get("snippet") or ""
    kg = data.get("knowledgeGraph") or {}
    if not answer_text and kg.get("description"):
        answer_text = kg["description"]
    # Google order is already relevance-ranked; keep it (higher score first).
    organic = data.get("organic") or []
    for i, item in enumerate(organic[:10]):
        candidates.append((10 - i, item.get("link", ""), item.get("title", ""), item.get("snippet", "")))
    return candidates, answer_text


_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Sentinel returned when the search engine blocked us (anti-bot 202 / captcha)
DDG_BLOCKED = "__DDG_BLOCKED__"


def _clean_html(s: str) -> str:
    import re
    s = re.sub(r'<[^>]+>', '', s)
    return s.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').strip()


def _score_candidates(query, rows):
    import re
    q_tokens = set(w for w in re.findall(r'[\w\d]+', query.lower(), re.UNICODE) if len(w) >= 3)
    out = []
    for url, title, snippet in rows[:10]:
        hay = f"{title} {snippet} {url}".lower()
        out.append((sum(1 for t in q_tokens if t in hay), url, title, snippet))
    out.sort(key=lambda c: c[0], reverse=True)
    return out


def _ddg_search(query):
    """DuckDuckGo search with retries, UA rotation and html+lite fallback.
    Returns a list of candidates, or DDG_BLOCKED if the engine blocked us."""
    import httpx, re, random, time
    blocked = False
    for attempt in range(3):
        ua = _UAS[attempt % len(_UAS)]
        hdr = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml",
               "Accept-Language": "uk,en;q=0.9", "Referer": "https://duckduckgo.com/"}
        # endpoint 1: html
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True, headers=hdr) as c:
                r = c.post("https://html.duckduckgo.com/html/",
                           data={"q": query, "kl": "ua-uk"})
            html = r.text
            if r.status_code == 200 and "result__url" in html:
                urls = re.findall(r'class="result__url"[^>]*href="([^"]+)"', html, re.I)
                titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.I | re.S)
                snips = re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.I | re.S)
                rows = [(urls[i], _clean_html(titles[i]) if i < len(titles) else "",
                         _clean_html(snips[i]) if i < len(snips) else "") for i in range(len(urls))]
                if rows:
                    return _score_candidates(query, rows)
            else:
                blocked = True
        except Exception as e:
            logger.warning(f"DDG html attempt {attempt}: {e}")
        # endpoint 2: lite
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True, headers=hdr) as c:
                r = c.post("https://lite.duckduckgo.com/lite/", data={"q": query, "kl": "ua-uk"})
            html = r.text
            if r.status_code == 200:
                pairs = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>', html, re.I | re.S)
                snips = re.findall(r'class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', html, re.I | re.S)
                rows = [(pairs[i][0], _clean_html(pairs[i][1]),
                         _clean_html(snips[i]) if i < len(snips) else "") for i in range(len(pairs))]
                if rows:
                    return _score_candidates(query, rows)
            else:
                blocked = True
        except Exception as e:
            logger.warning(f"DDG lite attempt {attempt}: {e}")
        time.sleep(0.6 + random.random())
    return DDG_BLOCKED if blocked else []


def web_research(query: str, max_pages: int = 3, page_chars: int = 4000, serper_key: str = None) -> str:
    """
    Deep web research for the agent: search (Google via Serper when a key is
    configured, DuckDuckGo otherwise), rank result links by relevance, then
    actually OPEN the best pages and extract their full readable text — not
    just snippets.
    """
    logger.info(f"Web research ({'serper' if serper_key else 'ddg'}) for: {query}")
    answer_text = ""
    try:
        candidates = []
        if serper_key:
            try:
                candidates, answer_text = _serper_search(query, serper_key)
            except Exception as e:
                logger.warning(f"Serper search failed, falling back to DDG: {e}")
        if not candidates:
            candidates = _ddg_search(query)
        if candidates == DDG_BLOCKED:
            return ("ПОШУК ЗАБЛОКОВАНО: DuckDuckGo тимчасово блокує запити (анти-бот). "
                    "Додайте Serper API ключ у Налаштування → Пошук в інтернеті для стабільного пошуку через Google.")
        if not candidates:
            return "No search results found."

        parts = []
        if answer_text:
            parts.append(f"=== GOOGLE ANSWER BOX:\n{answer_text}")
        # Compact search snippets first — on price aggregators they hold the
        # prices cleanly, while full pages are mostly navigation noise.
        snip_lines = [f"- {title}: {snippet}  ({url})" for score, url, title, snippet in candidates[:6] if snippet]
        if snip_lines:
            parts.append("=== РЕЗУЛЬТАТИ ПОШУКУ (сніпети з цінами):\n" + "\n".join(snip_lines))
        opened = 0
        for score, url, title, snippet in candidates:
            if opened >= max_pages:
                break
            page_text = fetch_and_parse_url(url, max_chars=page_chars)
            if page_text and not page_text.startswith("Error fetching URL") and "Could not extract" not in page_text:
                parts.append(f"=== ЗМІСТ СТОРІНКИ {title or url} ({url}):\n{page_text}")
                opened += 1

        if not parts:
            return "Found links but could not extract content from any page."
        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"Error during web research: {e}")
        return f"Search error: {e}"


def _extract_structured_prices(html: str) -> list:
    """Pull structured product prices from a page's markup instead of guessing
    from text: JSON-LD Product/Offer, og:product:price, microdata itemprop.
    Returns a list of {name, price, currency, availability}. Reliable because the
    price is bound to the product in the page's own structured data."""
    import re, json
    out = []
    seen = set()

    def add(name, price, currency="", avail=""):
        price = str(price or "").strip()
        if not price or not re.search(r"\d", price):
            return
        key = (str(name)[:80].lower(), price)
        if key in seen:
            return
        seen.add(key)
        out.append({"name": str(name or "").strip()[:140], "price": price,
                    "currency": str(currency or "").strip(), "availability": str(avail or "").strip()})

    # 1) JSON-LD (Product / Offer / @graph)
    for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in list(items):
            if isinstance(it, dict) and isinstance(it.get("@graph"), list):
                items.extend(it["@graph"])
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("@type") not in ("Product", "Offer") and "offers" not in it:
                continue
            name = it.get("name") or it.get("title") or ""
            offers = it.get("offers") or it
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                add(name, offers.get("price") or offers.get("lowPrice"),
                    offers.get("priceCurrency"),
                    (offers.get("availability") or "").split("/")[-1])

    # 2) OpenGraph / microdata fallback (single product page)
    if not out:
        og = (re.search(r'property=["\']product:price:amount["\']\s+content=["\']([\d.,]+)["\']', html, re.I)
              or re.search(r'itemprop=["\']price["\'][^>]*content=["\']([\d.,]+)["\']', html, re.I))
        if og:
            title = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
            cur = re.search(r'property=["\']product:price:currency["\']\s+content=["\']([^"\']+)["\']', html, re.I)
            add(title.group(1).strip() if title else "", og.group(1), cur.group(1) if cur else "")

    # 3) data-price (Bitrix and similar). On a single product page the main
    # data-price is the product's price and <title> is its name. Used last, only
    # when no JSON-LD/og price was found.
    if not out:
        dp = re.search(r'data-price=["\']?(\d[\d\s.,]*)["\']?', html)
        if dp:
            title = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
            add(title.group(1).strip() if title else "", dp.group(1).strip())

    return out[:8]


def fetch_and_parse_url(url: str, max_chars: int = 4000) -> str:
    """
    Fetches an HTML page. Returns the page's STRUCTURED product prices first
    (JSON-LD / og:price / microdata — bound to the product, not guessed), then the
    readable text. Falls back to plain text when no structured price is present.
    """
    import httpx
    import trafilatura
    logger.info(f"Fetching URL: {url}")
    try:
        html = None
        headers = {"User-Agent": _UAS[0], "Accept-Language": "uk,en;q=0.9"}
        with httpx.Client(timeout=12.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text

        parts = []
        structured = _extract_structured_prices(html) if html else []
        if structured:
            lines = []
            for p in structured:
                tail = []
                if p["currency"]:
                    tail.append(p["currency"])
                if p["availability"]:
                    tail.append(p["availability"])
                suffix = f" ({', '.join(tail)})" if tail else ""
                lines.append(f"- {p['name'] or 'товар'}: {p['price']} грн{suffix}")
            parts.append("СТРУКТУРОВАНІ ЦІНИ (з розмітки сторінки — товар↔ціна):\n" + "\n".join(lines))

        text = trafilatura.extract(html, include_links=True) if html else None
        if text:
            parts.append(text[:max_chars])
        if parts:
            return "\n\n".join(parts)

        # Last resort: trafilatura's own downloader (some sites block httpx)
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_links=True)
            if text:
                return text[:max_chars]
        return "Could not extract text from page."
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return f"Error fetching URL: {e}"
