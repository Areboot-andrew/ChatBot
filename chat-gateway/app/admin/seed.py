from sqlalchemy.future import select
from app.database import async_session_maker
from app.models.auth import User
from app.admin.auth import hash_password
from app.config import settings
import logging

logger = logging.getLogger(__name__)

async def seed_admin():
    async with async_session_maker() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalars().first()
        
        if not admin:
            logger.info("Creating default admin user...")
            hashed_pw = hash_password(settings.ADMIN_DEFAULT_PASSWORD)
            new_admin = User(username="admin", hashed_password=hashed_pw)
            db.add(new_admin)
            await db.commit()
            logger.info("Default admin user created successfully.")
