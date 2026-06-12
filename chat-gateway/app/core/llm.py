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

async def chat(
    messages: list, 
    model: str = settings.LLM_MODEL, 
    temperature: float = 0.7, 
    max_tokens: int = 1024,
    base_url: str = None,
    api_key: str = None,
    return_usage: bool = False,
    raise_error: bool = False,
    fallback_text: str = None
):
    """Sends a chat completion request to LM Studio or a custom API."""
    usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        if base_url:
            dynamic_client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or "not-needed"
            )
            response = await dynamic_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0
            )
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0
            )
            
        if response.usage:
            usage_data["prompt_tokens"] = response.usage.prompt_tokens or 0
            usage_data["completion_tokens"] = response.usage.completion_tokens or 0
            usage_data["total_tokens"] = response.usage.total_tokens or 0
            
        raw_content = response.choices[0].message.content
        clean_content = strip_think(raw_content)
        
        if return_usage:
            return clean_content, usage_data
        return clean_content
    except Exception as e:
        logger.error(f"LLM Chat Error: {e}")
        if raise_error:
            raise e
        err_msg = fallback_text or "Service temporarily unavailable."
        if return_usage:
            return err_msg, usage_data
        return err_msg

async def chat_stream(
    messages: list, 
    model: str = settings.LLM_MODEL, 
    temperature: float = 0.7, 
    max_tokens: int = 1024,
    base_url: str = None,
    api_key: str = None,
    fallback_text: str = None
):
    """Sends a streaming chat completion request and yields tokens."""
    try:
        if base_url:
            dynamic_client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or "not-needed"
            )
            response = await dynamic_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0,
                stream=True
            )
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0,
                stream=True
            )
            
        async for chunk in response:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
    except Exception as e:
        logger.error(f"LLM Chat Stream Error: {e}")
        yield fallback_text or "Service temporarily unavailable."

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
