import json
import logging
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import SessionBan
from app.models.channel import Channel

logger = logging.getLogger(__name__)


def split_chat_key(chat_key: str) -> tuple[str, uuid.UUID | None, str]:
    parts = str(chat_key or "").split(":", 2)
    channel_type = parts[0] if parts else "unknown"
    channel_id = None
    external_chat_id = parts[-1] if parts else str(chat_key or "")
    if len(parts) >= 3:
        try:
            channel_id = uuid.UUID(parts[1])
        except (ValueError, TypeError):
            channel_id = None
    return channel_type, channel_id, external_chat_id


async def record_session_ban(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    chat_key: str,
    last_message: str,
    reason: str = "Repeated direct abuse after a warning",
):
    """Persist the current Redis-backed ban for admin visibility."""
    try:
        channel_type, channel_id, external_chat_id = split_chat_key(chat_key)
        result = await db.execute(select(SessionBan).where(
            SessionBan.tenant_id == tenant_id,
            SessionBan.chat_key == chat_key,
        ))
        ban = result.scalars().first()
        was_active = bool(ban and ban.active)
        if not ban:
            ban = SessionBan(tenant_id=tenant_id, chat_key=chat_key)
            db.add(ban)
        if not was_active:
            ban.banned_at = func.now()
        ban.channel_type = channel_type
        ban.channel_id = channel_id
        ban.external_chat_id = external_chat_id
        ban.reason = reason
        if last_message:
            ban.last_message = last_message[:1000]
        ban.active = True
        ban.unbanned_at = None
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("Could not persist session ban: %s", exc)


async def import_legacy_redis_bans(db: AsyncSession, tenant_id: uuid.UUID):
    """Bring bans created before the SQL registry existed into the admin list."""
    from app.core.history import redis_client

    try:
        result = await db.execute(select(Channel.id).where(Channel.tenant_id == tenant_id))
        channel_ids = {str(value) for value in result.scalars().all()}
        if not channel_ids:
            return
        existing_result = await db.execute(select(SessionBan.chat_key).where(
            SessionBan.tenant_id == tenant_id,
            SessionBan.active == True,
        ))
        existing_keys = set(existing_result.scalars().all())
        async for redis_key in redis_client.scan_iter(match="memory:*", count=200):
            chat_key = str(redis_key).removeprefix("memory:")
            if chat_key in existing_keys:
                continue
            _, channel_id, _ = split_chat_key(chat_key)
            if not channel_id or str(channel_id) not in channel_ids:
                continue
            raw = await redis_client.get(redis_key)
            try:
                memory = json.loads(raw or "{}")
            except (ValueError, TypeError):
                continue
            if memory.get("_session_banned") == "1":
                await record_session_ban(
                    db, tenant_id, chat_key, "",
                    reason="Active ban imported from session memory",
                )
    except Exception as exc:
        logger.error("Could not import legacy Redis bans: %s", exc)
