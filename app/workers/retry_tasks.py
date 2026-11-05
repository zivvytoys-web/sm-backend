from app.workers.celery_app import celery_app
from app.db.session import SessionLocal
from app.models.publish_attempt import PublishAttempt
from app.models.scheduled_post import ScheduledPost
from app.utils.enums import PublishAttemptStatus, ContentStatus
from app.core.logging import logger


@celery_app.task(name="app.workers.retry_tasks.retry_failed_publications")
def retry_failed_publications():
    """Worker task to auto-retry posts that failed on previous network attempts."""
    db = SessionLocal()
    try:
        failed_posts = db.query(ScheduledPost).filter(
            ScheduledPost.status == ContentStatus.FAILED
        ).limit(10).all()

        for post in failed_posts:
            attempts_count = db.query(PublishAttempt).filter(
                PublishAttempt.scheduled_post_id == post.id
            ).count()

            if attempts_count < 3:
                logger.info(f"Re-queuing failed post #{post.id} (attempt {attempts_count + 1})")
                post.status = ContentStatus.SCHEDULED
                db.commit()
    finally:
        db.close()
