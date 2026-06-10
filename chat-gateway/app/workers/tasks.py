from arq.connections import RedisSettings
from app.config import settings
import logging

logger = logging.getLogger(__name__)

async def startup(ctx):
    logger.info("Worker started")

async def shutdown(ctx):
    logger.info("Worker shutting down")

async def llm_task(ctx, channel_id: str, chat_id: str, text: str):
    """
    Background task to process LLM request sequentially (concurrency 1)
    """
    logger.info(f"Processing LLM task for {channel_id}:{chat_id}")
    # TODO: Implement full pipeline (Intent -> RAG/SQL -> Prompt -> LLM -> Send)
    from app.core.llm import chat
    response = await chat([{"role": "user", "content": text}])
    logger.info(f"LLM Response: {response}")
    return response

# Parse REDIS_URL to get host/port
# e.g., redis://localhost:6379/0
url = settings.REDIS_URL.replace("redis://", "")
host = url.split(":")[0] if ":" in url else "localhost"
port = int(url.split(":")[1].split("/")[0]) if ":" in url else 6379

class WorkerSettings:
    functions = [llm_task]
    redis_settings = RedisSettings(host=host, port=port)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1 # Important: Concurrency 1 for local LLM
