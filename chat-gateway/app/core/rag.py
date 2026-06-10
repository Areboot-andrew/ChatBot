import re
import uuid
import logging
from typing import List
from app.database import qdrant
from app.core.llm import embed
from qdrant_client.http.models import PointStruct

logger = logging.getLogger(__name__)

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Простий алгоритм розбиття тексту на шматки.
    В реальності краще використовувати LangChain TextSplitter, але для M3 цього достатньо.
    """
    # Remove excessive newlines
    text = re.sub(r'\n+', '\n', text)
    words = text.split(' ')
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i:i + chunk_size]
        chunks.append(" ".join(chunk_words))
        i += chunk_size - overlap
    return chunks

async def process_and_vectorize_document(tenant_id: str, doc_id: str, title: str, text: str):
    """
    Фонова задача: розбиває текст, отримує ембединги і зберігає в Qdrant.
    """
    logger.info(f"Starting vectorization for doc {doc_id} ({title})")
    try:
        chunks = chunk_text(text)
        points = []
        
        for i, chunk in enumerate(chunks):
            # В майбутньому: якщо ембединг падає, зробити retry
            vector = await embed(chunk)
            if not vector:
                continue
                
            point_id = str(uuid.uuid4())
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "tenant_id": str(tenant_id),
                        "doc_id": str(doc_id),
                        "title": title,
                        "content": chunk,
                        "chunk_index": i
                    }
                )
            )
        
        if points:
            await qdrant.upsert(
                collection_name="knowledge_base",
                points=points
            )
            logger.info(f"Successfully indexed doc {doc_id} ({len(points)} chunks)")
        else:
            logger.warning(f"No points generated for doc {doc_id}")
            
    except Exception as e:
        logger.error(f"Error vectorizing document {doc_id}: {e}")
