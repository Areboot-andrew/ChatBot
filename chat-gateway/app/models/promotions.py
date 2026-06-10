import uuid
from sqlalchemy import Column, String, Numeric, Boolean, DateTime, func, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    
    discount_type = Column(String, nullable=True) # 'percent', 'fixed', 'bundle', 'gift'
    discount_value = Column(Numeric, nullable=True)
    
    conditions = Column(JSONB, default=dict)
    
    starts_at = Column(DateTime(timezone=True), nullable=True)
    ends_at = Column(DateTime(timezone=True), nullable=True)
    
    enabled = Column(Boolean, default=True)
    meta = Column(JSONB, default=dict)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

class PromotionProduct(Base):
    __tablename__ = "promotion_products"

    promotion_id = Column(UUID(as_uuid=True), ForeignKey("promotions.id", ondelete="CASCADE"), primary_key=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)

class ProductRelation(Base):
    __tablename__ = "product_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    source_product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    target_product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    
    relation_type = Column(String, nullable=False) # 'cross_sell', 'upsell', 'accessory', 'replacement', 'bundle'
    priority = Column(Integer, default=0)
    meta = Column(JSONB, default=dict)
