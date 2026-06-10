import uuid
import json
from passlib.hash import argon2
from datetime import datetime
from app.core.history import redis_client

SESSION_TTL = 86400  # 24 hours

def hash_password(password: str) -> str:
    return argon2.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return argon2.verify(password, hashed)

async def create_session(user_id: str, username: str) -> str:
    token = str(uuid.uuid4())
    key = f"session:{token}"
    data = {
        "user_id": str(user_id),
        "username": username,
        "created_at": datetime.utcnow().isoformat()
    }
    await redis_client.setex(key, SESSION_TTL, json.dumps(data))
    return token

async def get_session(token: str) -> dict | None:
    if not token:
        return None
    key = f"session:{token}"
    data = await redis_client.get(key)
    if data:
        # Refresh TTL on access
        await redis_client.expire(key, SESSION_TTL)
        return json.loads(data)
    return None

async def delete_session(token: str):
    if token:
        await redis_client.delete(f"session:{token}")
