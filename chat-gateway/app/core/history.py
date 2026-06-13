import json
import redis.asyncio as redis
from app.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

class HistoryManager:
    TTL = 72 * 3600 # 72 hours
    MAX_MESSAGES = 20

    @staticmethod
    def _key(channel_id: str, chat_id: str) -> str:
        return f"dialog:{channel_id}:{chat_id}"

    @classmethod
    async def get_history(cls, channel_id: str, chat_id: str) -> list:
        key = cls._key(channel_id, chat_id)
        raw_list = await redis_client.lrange(key, 0, cls.MAX_MESSAGES - 1)
        # Redis lrange returns latest if we push to L, or oldest if we push to R.
        # Assuming we push to right (RPUSH), the list is chronological.
        return [json.loads(msg) for msg in raw_list]

    @classmethod
    async def add_message(cls, channel_id: str, chat_id: str, role: str, content: str):
        key = cls._key(channel_id, chat_id)
        msg = json.dumps({"role": role, "content": content})
        
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, msg)
            pipe.ltrim(key, -cls.MAX_MESSAGES, -1) # Keep only last MAX_MESSAGES
            pipe.expire(key, cls.TTL)
            await pipe.execute()

    @classmethod
    async def clear_history(cls, channel_id: str, chat_id: str):
        key = cls._key(channel_id, chat_id)
        await redis_client.delete(key)


class MemoryManager:
    """Per-chat durable facts written by the agent loop (memory_patch)."""
    TTL = 72 * 3600

    @staticmethod
    def _key(chat_key: str) -> str:
        return f"memory:{chat_key}"

    @classmethod
    async def get_memory(cls, chat_key: str) -> dict:
        raw = await redis_client.get(cls._key(chat_key))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}

    @classmethod
    async def save_memory(cls, chat_key: str, memory: dict):
        key = cls._key(chat_key)
        if memory:
            await redis_client.set(key, json.dumps(memory, ensure_ascii=False), ex=cls.TTL)
        else:
            await redis_client.delete(key)

    @classmethod
    async def remove_ban(cls, chat_key: str):
        memory = await cls.get_memory(chat_key)
        memory.pop("_session_banned", None)
        memory.pop("_conduct_warning", None)
        await cls.save_memory(chat_key, memory)
