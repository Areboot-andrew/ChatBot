"""
Telegram UserBot channel (personal account via Telethon).

Reply-only by design to stay low-risk:
- answers ONLY incoming private messages (no groups, no channels, no bots);
- human-like behavior: random + length-based delay with "typing..." status;
- runs through the same pipeline as every other channel (same persona,
  prices, knowledge, per-chat agent memory).
"""
import asyncio
import logging
import random
import uuid

logger = logging.getLogger(__name__)


class UserbotManager:
    def __init__(self):
        self.clients = {}  # channel_id -> TelegramClient
        self._lock = asyncio.Lock()

    async def start_all(self):
        """Connect all enabled telegram_userbot channels."""
        from sqlalchemy import select
        from app.database import async_session_maker
        from app.models.channel import Channel

        async with async_session_maker() as db:
            res = await db.execute(
                select(Channel).where(Channel.type == 'telegram_userbot', Channel.enabled == True))
            channels = res.scalars().all()

        for ch in channels:
            try:
                await self._start_channel(ch.id, ch.tenant_id, ch.credentials or {})
            except Exception as e:
                logger.error(f"Userbot channel {ch.id} failed to start: {e}")

        if channels:
            logger.info(f"Userbot manager: {len(self.clients)}/{len(channels)} channels online")

    async def _start_channel(self, channel_id: uuid.UUID, tenant_id: uuid.UUID, creds: dict):
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        api_id = creds.get("api_id")
        api_hash = creds.get("api_hash")
        session_string = creds.get("session_string")
        if not (api_id and api_hash and session_string):
            logger.warning(f"Userbot channel {channel_id}: missing api_id/api_hash/session_string, skipping")
            return

        client = TelegramClient(StringSession(session_string), int(api_id), api_hash)

        @client.on(events.NewMessage(incoming=True))
        async def handler(event):
            await self._handle_message(client, channel_id, tenant_id, event)

        await client.connect()
        if not await client.is_user_authorized():
            logger.error(f"Userbot channel {channel_id}: session string is not authorized (regenerate it)")
            await client.disconnect()
            return

        self.clients[channel_id] = client
        logger.info(f"Userbot channel {channel_id} connected")

    async def _handle_message(self, client, channel_id, tenant_id, event):
        try:
            # Reply-only safety: private chats with real humans, never initiate.
            if not event.is_private or event.out:
                return
            sender = await event.get_sender()
            if sender is None or getattr(sender, "bot", False):
                return
            text = (event.raw_text or "").strip()
            if not text:
                return

            chat_id = str(event.chat_id)
            hist_channel = f"tg_user:{channel_id}"
            chat_key = f"{hist_channel}:{chat_id}"

            from app.core.history import HistoryManager
            from app.core.pipeline import process_message_pipeline
            from app.database import async_session_maker

            history = await HistoryManager.get_history(hist_channel, chat_id)
            history = history + [{"role": "user", "content": text}]

            # Human-like pacing: think a bit, then "type" while working.
            await asyncio.sleep(random.uniform(2, 5))
            async with client.action(event.chat_id, 'typing'):
                async with async_session_maker() as db:
                    response = await process_message_pipeline(
                        text, history, tenant_id, db, chat_key=chat_key)
                # Typing time roughly proportional to answer length, capped.
                await asyncio.sleep(min(1 + len(response) * 0.04, 8))

            await HistoryManager.add_message(hist_channel, chat_id, "user", text)
            await HistoryManager.add_message(hist_channel, chat_id, "assistant", response)

            await event.respond(response)
        except Exception:
            logger.exception(f"Userbot channel {channel_id}: error handling message")

    async def stop_all(self):
        for channel_id, client in list(self.clients.items()):
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(f"Userbot channel {channel_id} disconnect error: {e}")
        self.clients = {}

    async def restart(self):
        """Reconnect after channel create/edit in the admin panel."""
        async with self._lock:
            await self.stop_all()
            await self.start_all()


userbot_manager = UserbotManager()
