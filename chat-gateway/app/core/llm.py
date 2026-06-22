import httpx
from openai import AsyncOpenAI
import re
from app.config import normalize_lmstudio_url, settings
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
    fallback_text: str = None,
    json_mode: bool = False
):
    """Sends a chat completion request to LM Studio or a custom API.
    json_mode=True asks the provider to return strict JSON (response_format)."""
    usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    extra = {}
    if json_mode:
        extra["response_format"] = {"type": "json_object"}
    try:
        base_url = normalize_lmstudio_url(base_url)
        if base_url:
            extra_headers = {}
            if "openrouter.ai" in base_url:
                extra_headers["HTTP-Referer"] = getattr(settings, "PUBLIC_BASE_URL", "https://chat.texno.plus")
                extra_headers["X-Title"] = "ChatGateway"

            dynamic_client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or "not-needed",
                default_headers=extra_headers if extra_headers else None
            )
            response = await dynamic_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0,
                **extra
            )
        else:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=60.0,
                **extra
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
        base_url = normalize_lmstudio_url(base_url)
        if base_url:
            extra_headers = {}
            if "openrouter.ai" in base_url:
                extra_headers["HTTP-Referer"] = getattr(settings, "PUBLIC_BASE_URL", "https://chat.texno.plus")
                extra_headers["X-Title"] = "ChatGateway"

            dynamic_client = AsyncOpenAI(
                base_url=base_url,
                api_key=api_key or "not-needed",
                default_headers=extra_headers if extra_headers else None
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

_resolved_embed_model = None


async def _auto_embed_model() -> str:
    """Find an embedding model actually loaded in LM Studio (cached).

    So the operator does NOT have to type the exact model id: if both a chat model
    (gemma) and an embedding model are loaded, we auto-pick the embedding one by its
    id pattern. Falls back to the configured EMBED_MODEL if nothing matches.
    """
    global _resolved_embed_model
    if _resolved_embed_model:
        return _resolved_embed_model
    try:
        models = await client.models.list()
        ids = [m.id for m in models.data]
        for mid in ids:
            low = mid.lower()
            if any(k in low for k in ("embed", "bge", "e5", "gte", "nomic")):
                _resolved_embed_model = mid
                logger.info(f"Auto-selected embed model: {mid}")
                return mid
    except Exception as e:
        logger.warning(f"embed model autodetect failed: {e}")
    return settings.EMBED_MODEL


async def embed(text: str, model: str = None) -> list[float]:
    """Generates embeddings using LM Studio. If no model is given, auto-detect the
    embedding model loaded in LM Studio (panel field meta.embed_model overrides)."""
    name = (model or "").strip()
    if not name:
        name = await _auto_embed_model()
    try:
        response = await client.embeddings.with_options(max_retries=0).create(
            input=text,
            model=name,
            timeout=15.0
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"LLM Embed Error (model={name}): {e}")
        return []
