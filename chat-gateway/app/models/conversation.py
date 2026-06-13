import uuid
from sqlalchemy import Column, String, DateTime, func, ForeignKey, Boolean, UniqueConstraint
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


class SessionBan(Base):
    __tablename__ = "session_bans"
    __table_args__ = (UniqueConstraint("tenant_id", "chat_key", name="uq_session_ban_tenant_chat_key"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    chat_key = Column(String, nullable=False)
    channel_type = Column(String, nullable=False)
    external_chat_id = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    last_message = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    banned_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    unbanned_at = Column(DateTime(timezone=True), nullable=True)

class Operator(Base):
    __tablename__ = "operators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    name = Column(String, nullable=False)
    tg_chat_id = Column(String, nullable=True) # Where to send escalations
    
    meta = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
