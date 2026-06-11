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
