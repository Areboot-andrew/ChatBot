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


def _ddg_search(query: str) -> list:
    """DuckDuckGo HTML search. Returns candidates ranked by token overlap."""
    import httpx
    import urllib.parse
    import re
    data = urllib.parse.urlencode({'q': query})
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    with httpx.Client(timeout=10.0, follow_redirects=True, headers=headers) as client:
        resp = client.post("https://html.duckduckgo.com/html/", content=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp.raise_for_status()
        html = resp.text

    urls_raw = re.findall(r'class="result__url"[^>]*href="([^"]+)"', html, re.IGNORECASE)
    titles_raw = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
    snippets_raw = re.findall(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.IGNORECASE | re.DOTALL)
    if not urls_raw:
        return []

    def clean(s: str) -> str:
        s = re.sub(r'<[^>]+>', '', s)
        return s.replace('&#x27;', "'").replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').strip()

    q_tokens = set(w for w in re.findall(r'[\w\d]+', query.lower(), re.UNICODE) if len(w) >= 3)
    candidates = []
    for i, url in enumerate(urls_raw[:10]):
        title = clean(titles_raw[i]) if i < len(titles_raw) else ""
        snippet = clean(snippets_raw[i]) if i < len(snippets_raw) else ""
        haystack = f"{title} {snippet} {url}".lower()
        score = sum(1 for t in q_tokens if t in haystack)
        candidates.append((score, url, title, snippet))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates


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
        if not candidates:
            return "No search results found."

        parts = []
        if answer_text:
            parts.append(f"=== GOOGLE ANSWER BOX:\n{answer_text}")
        opened = 0
        for score, url, title, snippet in candidates:
            if opened >= max_pages:
                break
            page_text = fetch_and_parse_url(url, max_chars=page_chars)
            if page_text and not page_text.startswith("Error fetching URL") and "Could not extract" not in page_text:
                parts.append(f"=== ДЖЕРЕЛО: {title or url}\nURL: {url}\nЗМІСТ СТОРІНКИ:\n{page_text}")
                opened += 1
            else:
                # Page unreadable — keep at least the snippet as a weak fact.
                if snippet:
                    parts.append(f"=== ДЖЕРЕЛО (тільки сніпет): {title or url}\nURL: {url}\n{snippet}")

        if not parts:
            return "Found links but could not extract content from any page."
        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"Error during web research: {e}")
        return f"Search error: {e}"


def fetch_and_parse_url(url: str, max_chars: int = 4000) -> str:
    """
    Fetches an HTML page and extracts plain text.
    """
    import httpx
    import trafilatura
    logger.info(f"Fetching URL: {url}")
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_links=True)
            if text:
                return text[:max_chars]

        # Fallback to httpx if trafilatura fails to download
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text, include_links=True)
            if text:
                return text[:max_chars]
        return "Could not extract text from page."
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return f"Error fetching URL: {e}"
