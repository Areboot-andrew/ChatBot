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


def make_trace_collector(limit_chars: int = 0):
    """Returns (callback, steps). Pass callback as `trace` to the pipeline; the
    collected steps can be stored in Message.meta to show 'what the bot did'.

    limit_chars=0 (default) keeps full untruncated details so diagnostics can see
    exactly what the routes and the model received. Pass a positive value only if
    a specific storage budget requires truncation."""
    steps = []

    def collect(step, status, details, duration="-"):
        text = str(details)
        if limit_chars and len(text) > limit_chars:
            text = text[:limit_chars]
        steps.append({
            "step": step,
            "status": status,
            "details": text,
            "time": str(duration),
        })
    return collect, steps


def make_live_trace(tenant_id, chat_id, channel_type, limit_chars: int = 0):
    """Like make_trace_collector, but ALSO streams each step to the admin live
    feed the instant it fires. Returns (callback, steps): `steps` is still stored
    in Message.meta for the archive; the live publish is what makes the 'Жива
    стрічка' show the routes/model in real time instead of after the turn ends.

    `details` is never truncated for the live stream — diagnostics must see the
    full prompt the model received and its full raw answer."""
    from app.core.live_feed import publish
    collect, steps = make_trace_collector(limit_chars)

    def cb(step, status, details, duration="-"):
        collect(step, status, details, duration)
        publish(tenant_id, {
            "kind": "step",
            "chat_id": str(chat_id),
            "channel_type": channel_type,
            "step": step,
            "status": status,
            "details": str(details),
            "time": str(duration),
        })
    return cb, steps


def publish_live_message(tenant_id, chat_id, channel_type, role, content):
    """Push a client/bot message line to the live feed in real time."""
    from app.core.live_feed import publish
    publish(tenant_id, {
        "kind": "message",
        "chat_id": str(chat_id),
        "channel_type": channel_type,
        "role": role,
        "content": content or "",
    })


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
