from datetime import datetime
from sqlalchemy import Column, String, Boolean, Text, JSON, DateTime, Integer
from app.db.base import Base


class PlatformIntegration(Base):
    """
    Global Admin Platform Integration Configuration.
    Stores OAuth credentials, API endpoints, scopes, environment, and health testing results.
    Client secrets and API secrets are stored encrypted at rest using AES-256.
    """
    __tablename__ = "platform_integrations"

    platform = Column(String(50), unique=True, index=True, nullable=False)  # youtube, instagram, facebook, tiktok, linkedin, twitter, pinterest, snapchat
    display_name = Column(String(100), nullable=False)
    is_enabled = Column(Boolean, default=False, nullable=False)
    environment = Column(String(20), default="production", nullable=False)  # production / development
    
    # Credentials (secrets encrypted at rest)
    client_id = Column(String(255), nullable=True)
    client_secret_encrypted = Column(String(1024), nullable=True)
    api_key_encrypted = Column(String(1024), nullable=True)
    api_secret_encrypted = Column(String(1024), nullable=True)
    
    # Official Endpoints & Config
    auth_url = Column(String(512), nullable=True)
    token_url = Column(String(512), nullable=True)
    api_base_url = Column(String(512), nullable=True)
    api_version = Column(String(50), nullable=True)
    redirect_uri = Column(String(512), nullable=True)
    scopes = Column(JSON, default=list, nullable=False)
    webhook_url = Column(String(512), nullable=True)
    config_notes = Column(Text, nullable=True)
    
    # Platform Capabilities & Video Publishing Specifications
    extra_settings = Column(JSON, default=dict, nullable=False)
    
    # Health Verification Status
    last_tested_at = Column(DateTime, nullable=True)
    test_status = Column(String(50), default="untested", nullable=False)  # untested | healthy | failed
    test_message = Column(Text, nullable=True)
    last_updated_by = Column(Integer, nullable=True)
