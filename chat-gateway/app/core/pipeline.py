import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.tenant import BotSetting
import logging

logger = logging.getLogger(__name__)


def _noop_trace(step: str, status: str, details: str, duration: str = "-"):
    pass


async def process_message_pipeline(
    text: str,
    history: list,
    tenant_id: uuid.UUID,
    db: AsyncSession,
    trace=None,
    chat_key: str = None
) -> str:
    """
    Core pipeline to process an incoming message and return the LLM response.

    Runtime engine is the prompt-driven Lean isolated-route controller.

    `trace` is an optional callback(step, status, details, duration) used by the
    admin sandbox to visualize every step. The same pipeline serves all channels.
    `chat_key` (e.g. "telegram:12345") enables durable per-chat agent memory.
    """
    emit = trace or _noop_trace

    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res.scalars().first()

    from app.core.agent_lean import run_agent_lean as run_agent
    from app.core.history import MemoryManager

    memory = await MemoryManager.get_memory(chat_key) if chat_key else {}
    if memory.get("_session_banned") == "1":
        emit("SESSION", "Заблоковано", "Ця сесія забанена; відповідь приглушено.")
        return ""
    try:
        was_banned = memory.get("_session_banned") == "1"
        answer, new_memory = await run_agent(
            text, history, tenant_id, db, settings, trace=trace, memory=memory
        )
        if chat_key:
            await MemoryManager.save_memory(chat_key, new_memory)
            if not was_banned and new_memory.get("_session_banned") == "1":
                from app.core.bans import record_session_ban
                await record_session_ban(db, tenant_id, chat_key, text)
        return answer
    except Exception as e:
        logger.error(f"message pipeline failed: {e}")
        emit("PIPELINE", "Помилка", str(e))
        return settings.fallback_text if settings and settings.fallback_text else "Технічна заминка, спробуйте ще раз."
