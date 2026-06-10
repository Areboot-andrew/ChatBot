import uuid
from sqlalchemy import Column, String, Integer, DateTime, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class PriceList(Base):
    __tablename__ = "price_list"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    device_type = Column(String, nullable=True)
    brand = Column(String, nullable=True)
    model = Column(String, nullable=True)
    model_normalized = Column(String, nullable=True)
    
    service = Column(String, nullable=False)
    price_min = Column(Integer, nullable=True)
    price_max = Column(Integer, nullable=True)
    duration = Column(String, nullable=True)
    note = Column(String, nullable=True)
    
    meta = Column(JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
