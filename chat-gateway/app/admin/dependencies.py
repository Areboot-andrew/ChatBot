from fastapi import Request, HTTPException, status, Cookie
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import uuid

from app.admin.auth import get_session
from app.models.auth import User
from app.database import async_session_maker

async def get_current_user(request: Request) -> User:
    token = request.cookies.get("admin_session")
    session_data = await get_session(token)
    
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/admin/login"}
        )
        
    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.id == session_data["user_id"]))
        user = result.scalars().first()
        if not user or not user.is_active:
             raise HTTPException(
                status_code=status.HTTP_302_FOUND,
                headers={"Location": "/admin/login"}
            )
        return user

async def get_current_tenant_id(request: Request) -> Optional[uuid.UUID]:
    tenant_id_str = request.cookies.get("tenant_id")
    if not tenant_id_str:
        # try header
        tenant_id_str = request.headers.get("X-Tenant-Id")
    
    if tenant_id_str:
        try:
            return uuid.UUID(tenant_id_str)
        except ValueError:
            pass
    return None
