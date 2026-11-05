from PIL import TiffImagePlugin
import os
from typing import List, Union
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Social AI Platform"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    @field_validator("DEBUG", mode="before")
    def parse_debug_mode(cls, value):
        """Accept common deployment labels such as DEBUG=release/prod."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "production", "prod", "false", "0", "no", "off"}:
                return False
            if normalized in {"development", "dev", "true", "1", "yes", "on"}:
                return True
        return value

    # Security & JWT
    SECRET_KEY: str = "temporary-super-secret-key-change-in-production-1234567890"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # MySQL Database Connection
    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "password"
    MYSQL_DB: str = "social_ai_db"
    DATABASE_URL: str | None = None

    @field_validator("DATABASE_URL", mode="before")
    def assemble_db_connection(cls, v: str | None, values) -> str:
        from urllib.parse import quote_plus
        raw_url = (
            os.environ.get("DATABASE_URL")
            or os.environ.get("MYSQL_URL")
            or os.environ.get("MYSQL_PRIVATE_URL")
            or os.environ.get("MYSQLPUBLICURL")
            or os.environ.get("MYSQL_PUBLIC_URL")
            or v
        )
        if isinstance(raw_url, str) and raw_url.strip():
            url = raw_url.strip()
            # Railway and cloud providers inject mysql://, SQLAlchemy requires mysql+pymysql://
            if url.startswith("mysql://"):
                url = url.replace("mysql://", "mysql+pymysql://", 1)
            elif url.startswith("mysql2://"):
                url = url.replace("mysql2://", "mysql+pymysql://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg2://", 1)
            elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
                url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            return url

        host = os.environ.get("MYSQLHOST") or os.environ.get("MYSQL_HOST") or values.data.get('MYSQL_HOST', 'localhost')
        port = int(os.environ.get("MYSQLPORT") or os.environ.get("MYSQL_PORT") or values.data.get('MYSQL_PORT', 3306))
        user = os.environ.get("MYSQLUSER") or os.environ.get("MYSQL_USER") or values.data.get('MYSQL_USER', 'root')
        password = os.environ.get("MYSQLPASSWORD") or os.environ.get("MYSQL_PASSWORD") or values.data.get('MYSQL_PASSWORD', 'password')
        database = os.environ.get("MYSQLDATABASE") or os.environ.get("MYSQL_DATABASE") or os.environ.get("MYSQL_DB") or values.data.get('MYSQL_DB', 'social_ai_db')

        return f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{database}"

    # Redis & Celery
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
    ]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return []

    # AI Engine - Single Google Gemini API Key for Text, Image, and Video
    GEMINI_API_KEY: str = ""
    GEMINI_TEXT_MODEL: str = "gemini-1.5-flash"
    GEMINI_IMAGE_MODEL: str = "imagen-3.0-generate-002"
    GEMINI_VIDEO_MODEL: str = "veo-2.0-generate-001"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""  # Also used as VEO_API_KEY if VEO_API_KEY not set
    VEO_API_KEY: str = ""
    STABILITY_API_KEY: str = ""
    MAGIC_HOUR_API_KEY: str = ""

    # Cloudflare Workers AI / Flux image generation
    CLOUDFLARE_API_TOKEN: str = ""
    CLOUDFLARE_ACCOUNT_ID: str = "c72831fcea69f3a222ba29f96469742d"
    CLOUDFLARE_IMAGE_MODEL: str = "@cf/black-forest-labs/flux-1-schnell"
    FAL_KEY: str = "fal_sk_86811980a2b641a3878e607c2cbd5133:47aea1db27b49be05bb1017c28d3fb42"
    FAL_IMAGE_MODEL: str = "fal-ai/flux/schnell"
    LTX_API_KEY: str = ""
    HUGGINGFACE_API_TOKEN: str = ""
    HUGGINGFACE_VIDEO_SPACE: str = "ByteDance/AnimateDiff-Lightning"

    # Security & Encryption
    ENCRYPTION_KEY: str = ""
    TOKEN_ENCRYPTION_KEY: str = ""

    # Frontend URL
    FRONTEND_URL: str = "http://localhost:3000"

    # Social APIs - Meta (Instagram & Facebook)
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_GRAPH_VERSION: str = "v23.0"
    META_REDIRECT_URI: str = "http://127.0.0.1:8002/api/v1/social/facebook/callback"
    META_SCOPES: str = "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish"
    META_WEBHOOK_VERIFY_TOKEN: str = ""
    INSTAGRAM_CLIENT_ID: str = ""
    INSTAGRAM_CLIENT_SECRET: str = ""
    FACEBOOK_CLIENT_ID: str = ""
    FACEBOOK_CLIENT_SECRET: str = ""

    # Social APIs - Google / YouTube
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""

    # Social APIs - X (Twitter)
    TWITTER_CLIENT_ID: str = ""
    TWITTER_CLIENT_SECRET: str = ""
    TWITTER_API_KEY: str = ""
    TWITTER_API_SECRET: str = ""
    TWITTER_BEARER_TOKEN: str = ""

    # Social APIs - TikTok
    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""

    # Social APIs - LinkedIn
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""

    # Social APIs - Pinterest
    PINTEREST_APP_ID: str = ""
    PINTEREST_APP_SECRET: str = ""

    # Social APIs - Snapchat
    SNAPCHAT_CLIENT_ID: str = ""
    SNAPCHAT_CLIENT_SECRET: str = ""

    # Storage & Cloudinary
    STORAGE_TYPE: str = "cloudinary"  # 'cloudinary', 'local', or 's3'
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_S3_BUCKET: str = "social-ai-platform-assets"
    # Local disk is temporary-only for media processing. Persisted media must
    # be uploaded to Cloudinary; never use a project-level uploads directory.
    LOCAL_STORAGE_DIR: str = "./.media_tmp"

    # Cloudinary Integration (Production fallback defaults)
    CLOUDINARY_CLOUD_NAME: str = "e2yvcdttz"
    CLOUDINARY_API_KEY: str = "431648218683675"
    CLOUDINARY_API_SECRET: str = "W834W3_c-N1i78Qf4H6g6lq4jLU"

    # Stripe Payments
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"
    )


settings = Settings()
