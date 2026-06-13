"""
Persist chat messages to Postgres (conversations + messages) for the admin
'Діалоги' page: live feed + archive across all channels.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message

logger = logging.getLogger(__name__)


async def log_message(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    channel_id: uuid.UUID,
    external_chat_id: str,
    role: str,
    content: str,
    meta: dict = None,
):
    """Append a message to the (tenant, channel, chat) conversation, creating it
    if needed. Best-effort: never break the chat flow on a logging error."""
    try:
        res = await db.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.channel_id == channel_id,
                Conversation.external_chat_id == str(external_chat_id),
            )
        )
        conv = res.scalars().first()
        if not conv:
            conv = Conversation(
                tenant_id=tenant_id,
                channel_id=channel_id,
                external_chat_id=str(external_chat_id),
                status="bot",
            )
            db.add(conv)
            await db.flush()
        msg = Message(conversation_id=conv.id, role=role, content=content or "", meta=meta or {})
        db.add(msg)
        # touch conversation so ordering by recency works
        from sqlalchemy import func
        conv.updated_at = func.now()
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"log_message failed: {e}")
