import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from src.config.env import settings
from src.config.db import engine, Base
import app.models  # Ensure all SQLAlchemy models are registered on Base
from src.utils.logger import setup_logging, logger
from src.middleware.error_middleware import ErrorHandlingMiddleware, validation_exception_handler
from src.middleware.security_headers import SecurityHeadersMiddleware
from app.core.rate_limit import RateLimitMiddleware
from src.routes import api_router
# pyrefly: ignore[missing-import]
from app.utils.exceptions import AppException
from app.scheduler.publishing_worker import PublishingWorker
from app.scheduler.autonomous_scheduler import AutonomousScheduler
from app.db.seed import seed_database
# pyrefly: ignore[missing-import]
from app.db.session import SessionLocal

# Create only the temporary processing directory; persisted media lives in Cloudinary.
os.makedirs(settings.LOCAL_STORAGE_DIR, exist_ok=True)


def _ensure_schema_columns(eng):
    from sqlalchemy import text, inspect
    insp = inspect(eng)
    migrations = [
        ("social_accounts", "organization_id", "INT NULL"),
        ("contents", "organization_id", "INT NULL"),
        ("generation_jobs", "organization_id", "INT NULL"),
        ("audit_logs", "organization_id", "INT NULL"),
        ("business_schedules", "organization_id", "INT NULL"),
        ("business_schedules", "generation_date", "VARCHAR(10) NULL"),
        ("business_schedules", "posting_date", "VARCHAR(10) NULL"),
        ("business_schedules", "auto_schedule", "BOOLEAN DEFAULT FALSE"),
        ("business_schedules", "selected_social_account_ids", "JSON NULL"),
        ("business_schedules", "content_type", "VARCHAR(20) DEFAULT 'both'"),
        ("business_schedules", "content_preferences", "TEXT NULL"),
        ("pipeline_runs", "organization_id", "INT NULL"),
        ("pipeline_runs", "stage", "VARCHAR(50) NULL"),
        ("pipeline_runs", "progress_percent", "INT DEFAULT 0"),
        ("autonomous_posts", "organization_id", "INT NULL"),
        ("analytics", "views", "BIGINT NOT NULL DEFAULT 0"),
        ("contents", "sha256_hash", "VARCHAR(64) NULL"),
        ("contents", "validation_status", "VARCHAR(32) NOT NULL DEFAULT 'PENDING'"),
        ("contents", "quality_score", "FLOAT NOT NULL DEFAULT 100.0"),
        ("contents", "is_duplicate", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("media_assets", "sha256_hash", "VARCHAR(64) NULL"),
        ("media_assets", "perceptual_hash", "VARCHAR(64) NULL"),
        ("media_assets", "validation_status", "VARCHAR(32) NOT NULL DEFAULT 'PENDING'"),
        ("media_assets", "quality_score", "FLOAT NOT NULL DEFAULT 100.0"),
        ("media_assets", "is_duplicate", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("media_assets", "duplicate_of_id", "INT NULL"),
    ]
    with eng.connect() as conn:
        for table_name, col_name, col_def in migrations:
            if insp.has_table(table_name):
                cols = {c['name'] for c in insp.get_columns(table_name)}
                if col_name not in cols:
                    try:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                    except Exception:
                        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # If executing isolated tests, skip background daemon tasks and external MySQL retries
    if os.environ.get("TESTING") == "true":
        yield
        return

    # Startup lifecycle
    setup_logging()
    logger.info("Starting Social AI Platform backend application...")
    
    is_prod = getattr(settings, "ENVIRONMENT", "").lower() in ("production", "prod")
    auto_seed = os.environ.get("SEED_DATABASE", "false").lower() in ("true", "1", "yes")

    last_error = None
    for attempt in range(1, 16):
        db = None
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_schema_columns(engine)
            db = SessionLocal()
            if auto_seed:
                seed_database(db)
                logger.info("Database connected, tables initialized, and seed check completed.")
            else:
                logger.info("Database connected and tables initialized (auto-seed disabled in production).")
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            logger.warning(f"Database startup attempt {attempt}/15 failed: {exc}")
            engine.dispose()
            if attempt < 15:
                await asyncio.sleep(3)
        finally:
            if db is not None:
                db.close()

    if last_error is not None:
        logger.error(f"Database initialization deferred after 15 attempts: {last_error}")

    await AutonomousScheduler.start()
    worker_task = asyncio.create_task(PublishingWorker.start_background_loop(poll_interval_seconds=15))

    yield

    # Shutdown lifecycle
    AutonomousScheduler.shutdown()
    PublishingWorker.stop_background_loop()
    worker_task.cancel()
    logger.info("Shutting down Social AI Platform backend...")


# Initialize FastAPI App (app.py)
app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Security Headers Middleware
app.add_middleware(SecurityHeadersMiddleware)

# Rate Limiting & Abuse Prevention Middleware
app.add_middleware(RateLimitMiddleware)

# Error Middleware & Handlers
app.add_middleware(ErrorHandlingMiddleware)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# CORS Middleware - Outermost middleware so CORS headers are added to all responses
is_prod = getattr(settings, "ENVIRONMENT", "").lower() in ("production", "prod")
cors_origins = settings.BACKEND_CORS_ORIGINS if settings.BACKEND_CORS_ORIGINS else ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000"]
# Ensure Vercel production and preview domains are authorized
for origin in [
    "https://smm-frontend-ten.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173"
]:
    if origin not in cors_origins:
        cors_origins.append(origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After", "Content-Range", "Accept-Ranges"]
)


@app.exception_handler(AppException)
async def custom_app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "detail": exc.detail}
    )


# Root health check endpoint
@app.get("/", tags=["Health"])
def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "version": "1.0.0",
        "docs": "/docs"
    }


from app.api.routes.automation import router as direct_automation_router
from app.api.routes.webhooks import router as direct_webhooks_router

# Include API routes (single registration under /api/v1)
app.include_router(api_router, prefix=settings.API_V1_STR)
# Mount automation router at /api for exact compatibility with /api/automation/...
app.include_router(direct_automation_router, prefix="/api")
# Mount webhooks router at /api for external provider callbacks (/api/webhooks/...)
app.include_router(direct_webhooks_router, prefix="/api")

# Mount /uploads for static media and video streaming with HTTP 206 Partial Content / Range support
import os
from starlette.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi import HTTPException

os.makedirs(settings.LOCAL_STORAGE_DIR, exist_ok=True)

@app.get("/uploads/{file_path:path}")
async def stream_uploaded_media(file_path: str):
    """Stream local media with HTTP 206 Byte-Range support and CORS headers for HTML5 video/audio players."""
    full_path = os.path.join(settings.LOCAL_STORAGE_DIR, file_path)
    if not os.path.exists(full_path):
        # Also check media subfolder
        candidate = os.path.join(settings.LOCAL_STORAGE_DIR, "media", file_path)
        if os.path.exists(candidate):
            full_path = candidate
        else:
            raise HTTPException(status_code=404, detail="Media file not found")
    media_type = "video/mp4" if full_path.endswith(".mp4") else ("audio/mpeg" if full_path.endswith(".mp3") else None)
    return FileResponse(
        full_path,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Authorization"
        }
    )

app.mount("/uploads", StaticFiles(directory=settings.LOCAL_STORAGE_DIR), name="uploads")

__all__ = ["app"]
