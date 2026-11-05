from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from app.db.base import Base

# Many-to-Many Association Table between Collections and MediaAssets
collection_media = Table(
    "collection_media",
    Base.metadata,
    Column("collection_id", Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
    Column("media_asset_id", Integer, ForeignKey("media_assets.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime, default=datetime.utcnow, nullable=False)
)


class Collection(Base):
    __tablename__ = "collections"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    cover_asset_id = Column(Integer, ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    media_assets = relationship("MediaAsset", secondary=collection_media, back_populates="collections")
    cover_asset = relationship("MediaAsset", foreign_keys=[cover_asset_id])
    organization = relationship("Organization")
    user = relationship("User")
