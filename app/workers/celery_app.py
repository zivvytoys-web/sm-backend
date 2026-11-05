from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "social_ai_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.generation_tasks",
        "app.workers.publishing_tasks",
        "app.workers.analytics_tasks",
        "app.workers.retry_tasks",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        "check-scheduled-posts-every-minute": {
            "task": "app.workers.publishing_tasks.process_scheduled_posts",
            "schedule": 60.0,
        },
        "sync-analytics-daily": {
            "task": "app.workers.analytics_tasks.sync_all_social_analytics",
            "schedule": 86400.0,
        }
    }
)
