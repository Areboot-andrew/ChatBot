import logging
from duckduckgo_search import DDGS

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
            
            # Fetch the first page for deep context
            if urls_raw:
                try:
                    deep_text = fetch_and_parse_url(urls_raw[0])
                    if deep_text and "Error fetching URL" not in deep_text and "Could not extract" not in deep_text:
                        result_text += f"\n\n--- ДОДАТКОВИЙ ПАРСИНГ ПЕРШОГО САЙТУ ({urls_raw[0]}) ---\n"
                        # Limit to 2000 chars to save tokens and avoid prompt bloat
                        result_text += deep_text[:2000] 
                except Exception as ex:
                    logger.warning(f"Could not deep parse top URL: {ex}")
                    
            return result_text
    except Exception as e:
        logger.error(f"Error during web search: {e}")
        return f"Search error: {e}"

def fetch_and_parse_url(url: str) -> str:
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
                return text[:4000]
        
        # Fallback to httpx if trafilatura fails to download
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text, include_links=True)
            if text:
                return text[:4000]
        return "Could not extract text from page."
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return f"Error fetching URL: {e}"
