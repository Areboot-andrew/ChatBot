import uuid
from sqlalchemy import Column, String, DateTime, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), nullable=False)
    
    external_chat_id = Column(String, nullable=False)
    status = Column(String, default="bot") # 'bot', 'operator', 'closed'
    
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    
    role = Column(String, nullable=False) # 'user', 'assistant', 'operator'
    content = Column(String, nullable=False)
    
    meta = Column(JSONB, default=dict) # {intent, rag_doc_ids, latency_ms, tokens, etc}
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Operator(Base):
    __tablename__ = "operators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    name = Column(String, nullable=False)
    tg_chat_id = Column(String, nullable=True) # Where to send escalations
    
    meta = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
