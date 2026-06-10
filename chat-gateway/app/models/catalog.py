import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.database import Base

class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    sku = Column(String, nullable=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=True)
    brand = Column(String, nullable=True)
    model_normalized = Column(String, nullable=True)
    
    specs = Column(JSONB, default=dict)
    price = Column(Integer, nullable=True)
    in_stock = Column(Boolean, default=True)
    
    meta = Column(JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

class ProductCompat(Base):
    __tablename__ = "product_compat"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    compatible_model_normalized = Column(String, nullable=False)

class ProductTag(Base):
    __tablename__ = "product_tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False)
    meta = Column(JSONB, default=dict)

class ProductTagMap(Base):
    __tablename__ = "product_tag_map"

    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    tag_id = Column(UUID(as_uuid=True), ForeignKey("product_tags.id", ondelete="CASCADE"), primary_key=True)
