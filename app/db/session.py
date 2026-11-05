import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# Engine configuration with pooling suitable for production MySQL or isolated test SQLite
if os.environ.get("TESTING") == "true" or getattr(settings, "ENVIRONMENT", "").lower() == "testing":
    database_url = "sqlite:///./test.db"
else:
    database_url = settings.DATABASE_URL or "sqlite:///./app.db"

engine_options = {"echo": False}

# SQLite's default pool does not accept the MySQL queue-pool options below.
if database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    engine_options.update(
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=30,
        max_overflow=40,
        pool_timeout=10,
    )

engine = create_engine(database_url, **engine_options)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
