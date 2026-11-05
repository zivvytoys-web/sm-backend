import logging
import sys
from src.config.env import settings


def setup_logging():
    """Configure centralized logging format and handlers."""
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


logger = logging.getLogger("social_ai_platform")
__all__ = ["logger", "setup_logging"]
