import asyncio
from app.workers.celery_app import celery_app
from app.scheduler.publishing_worker import PublishingWorker
from app.core.logging import logger


@celery_app.task(name="app.workers.publishing_tasks.process_scheduled_posts")
def process_scheduled_posts():
    """Periodic worker task checking for due scheduled posts and publishing them."""
    loop = asyncio.new_event_loop()
    try:
        count = loop.run_until_complete(PublishingWorker.process_due_posts())
        logger.info(f"Celery task processed {count} due scheduled posts.")
        return count
    except Exception as e:
        logger.error(f"Error in Celery process_scheduled_posts: {e}")
        raise
    finally:
        loop.close()
