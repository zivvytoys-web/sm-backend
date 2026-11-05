from typing import Generator
"""Compatibility exports for the application's single SQLAlchemy engine."""
from app.db.session import engine, SessionLocal
from app.db.base import Base
from app.db.dependencies import get_db

__all__ = ["engine", "SessionLocal", "Base", "get_db"]

