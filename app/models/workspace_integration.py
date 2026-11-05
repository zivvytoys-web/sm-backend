from sqlalchemy import Column, String, Boolean, JSON, Text, ForeignKey, Integer, UniqueConstraint
from app.db.base import Base


class WorkspaceIntegration(Base):
    """Workspace-owned OAuth application configuration.

    Secrets are encrypted before persistence and are never included in API
    responses. Existing global provider settings remain available as a
    compatibility fallback for legacy tenants.
    """
    __tablename__ = "workspace_integrations"
    __table_args__ = (UniqueConstraint("organization_id", "provider", name="uq_workspace_provider"),)

    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(50), nullable=False, index=True)
    client_id = Column(String(255), nullable=True)
    client_secret_encrypted = Column(String(2048), nullable=True)
    settings = Column(JSON, default=dict, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    verification_status = Column(String(32), default="untested", nullable=False)
    verification_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
