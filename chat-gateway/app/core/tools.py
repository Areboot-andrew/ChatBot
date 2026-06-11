import logging
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

def search_internet(query: str, max_results: int = 3) -> str:
    """
    Performs a web search using DuckDuckGo and returns a formatted string of the best snippets.
    """
    logger.info(f"Searching internet for: {query}")
    try:
        ddgs = DDGS()
        results = ddgs.text(query, max_results=max_results)
        
        if not results:
            return "Результатів в інтернеті не знайдено."
            
        snippets = []
        for i, res in enumerate(results):
            title = res.get("title", "")
            body = res.get("body", "")
            href = res.get("href", "")
            snippets.append(f"Джерело {i+1}: {title}\nURL: {href}\nФрагмент: {body}")
            
        return "\n\n".join(snippets)
    except Exception as e:
        logger.error(f"Error during web search: {e}")
        return f"Помилка пошуку в інтернеті: {e}"

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
        return "Не вдалося витягти текст зі сторінки."
    except Exception as e:
        logger.error(f"Error fetching URL {url}: {e}")
        return f"Помилка завантаження сайту: {e}"
