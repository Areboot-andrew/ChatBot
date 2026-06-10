import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class KbDocument(Base):
    __tablename__ = "kb_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    title = Column(String, nullable=False)
    category = Column(String, nullable=True)
    source = Column(String, nullable=True) # 'upload', 'site'
    
    filename = Column(String, nullable=True)
    mime = Column(String, nullable=True)
    sha256 = Column(String, nullable=True)
    
    status = Column(String, nullable=False, default="pending")
    chunks_count = Column(Integer, default=0)
    
    meta = Column(JSONB, default=dict)
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

class QaPair(Base):
    __tablename__ = "qa_pairs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    question = Column(String, nullable=False)
    question_variants = Column(JSONB, default=list) # List of strings
    answer = Column(String, nullable=False)
    
    category = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    
    meta = Column(JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
