from typing import Generator
from sqlalchemy.orm import Session
from app.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Dependency that yields a database session for requests and closes it cleanly."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
