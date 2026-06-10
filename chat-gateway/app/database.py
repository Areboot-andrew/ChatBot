from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    future=True
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with async_session_maker() as session:
        yield session

from qdrant_client import AsyncQdrantClient

# Ініціалізуємо Qdrant клієнт
qdrant = AsyncQdrantClient(url=settings.QDRANT_URL)

async def init_qdrant_collections():
    """Створюємо колекцію для бази знань, якщо її немає"""
    try:
        collections = await qdrant.get_collections()
        collection_names = [c.name for c in collections.collections]
        
        if "knowledge_base" not in collection_names:
            from qdrant_client.http.models import VectorParams, Distance
            await qdrant.create_collection(
                collection_name="knowledge_base",
                vectors_config=VectorParams(size=768, distance=Distance.COSINE) # Nomic Embed Text typically outputs 768d vectors
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Qdrant Init Error: {e}")

