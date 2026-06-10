import uuid
from sqlalchemy import Column, String, Boolean, DateTime, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class Channel(Base):
    __tablename__ = "channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    type = Column(String, nullable=False) # 'telegram', 'viber', 'webchat'
    name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)
    
    credentials = Column(JSONB, default=dict)
    webhook_secret = Column(String, nullable=True)
    persona_override = Column(String, nullable=True)
    greeting = Column(String, nullable=True)
    
    meta = Column(JSONB, default=dict)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
