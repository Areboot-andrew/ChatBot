import uuid
from sqlalchemy import Column, String, Boolean, DateTime, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    meta = Column(JSONB, default=dict)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class BotSetting(Base):
    __tablename__ = "bot_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    
    system_prompt = Column(String, nullable=False)
    escalation_prompt = Column(String, nullable=True)
    fallback_text = Column(String, nullable=True)
    
    llm_model = Column(String, nullable=True)
    temperature = Column(String, nullable=True) # or Float
    max_tokens = Column(String, nullable=True) # or Int
    
    rag_top_k = Column(String, nullable=True)
    rag_score_threshold = Column(String, nullable=True)
    
    business_rules = Column(String, nullable=True) # Жорсткі директиви
    marketing_rules = Column(String, nullable=True) # Маркетингові/крос-сейл протоколи
    escalation_policy = Column(String, nullable=True, default="handoff") # google, handoff, info
    
    meta = Column(JSONB, default=dict)

class KnowledgeType(Base):
    __tablename__ = "knowledge_types"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_knowledge_type_tenant_code"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    code = Column(String, nullable=False)
    label = Column(String, nullable=False)
    handler = Column(String, nullable=False)
    intent_patterns = Column(JSONB, default=list) # Storing as list of strings
    priority = Column(String, nullable=True) # or Int
    enabled = Column(Boolean, default=True)
    meta = Column(JSONB, default=dict)
