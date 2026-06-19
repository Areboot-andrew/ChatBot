import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base

class ServiceCategory(Base):
    __tablename__ = "service_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    # "phones", "laptops", etc.
    slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    
    # Any additional metadata like keywords, metaTitle etc.
    meta = Column(JSONB, default=dict)
    
    enabled = Column(Boolean, default=True)
    
    # Relationships
    prices = relationship("ServicePrice", back_populates="category", cascade="all, delete-orphan")


class ServicePrice(Base):
    __tablename__ = "service_prices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    category_id = Column(UUID(as_uuid=True), ForeignKey("service_categories.id", ondelete="CASCADE"), nullable=False)
    
    name = Column(String, nullable=False) # e.g. "Заміна екрану"
    price = Column(String, nullable=False) # e.g. "1500 - 3000 грн" (string since it might be a range)
    description = Column(String, nullable=True) # human notes/conditions for the route model
    meta = Column(JSONB, default=dict) # universal item fields: type, brand, stock, specs, composition
    
    category = relationship("ServiceCategory", back_populates="prices")
