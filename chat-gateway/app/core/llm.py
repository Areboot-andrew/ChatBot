import httpx
from openai import AsyncOpenAI
import re
from app.config import settings
import logging

logger = logging.getLogger(__name__)

# Initialize OpenAI client with LM Studio base URL
client = AsyncOpenAI(
    base_url=settings.LMSTUDIO_URL,
    api_key="not-needed" # LM Studio doesn't require API key
)

def strip_think(text: str) -> str:
    """Removes <think>...</think> blocks from Gemma/DeepSeek models."""
    if not text:
        return ""
    # Use re.DOTALL to match across newlines
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

async def chat(messages: list, model: str = settings.LLM_MODEL, temperature: float = 0.7, max_tokens: int = 1024) -> str:
    """Sends a chat completion request to LM Studio."""
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=60.0
        )
        raw_content = response.choices[0].message.content
        return strip_think(raw_content)
    except Exception as e:
        logger.error(f"LLM Chat Error: {e}")
        return "Вибачте, зараз я не можу відповісти. Зачекайте хвилинку або зверніться пізніше."

async def embed(text: str, model: str = settings.EMBED_MODEL) -> list[float]:
    """Generates embeddings using LM Studio."""
    try:
        response = await client.embeddings.create(
            input=text,
            model=model,
            timeout=30.0
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"LLM Embed Error: {e}")
        return []
