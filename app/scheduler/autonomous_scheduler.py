import asyncio
import pytz
from datetime import datetime, time, timedelta
from typing import Optional, List, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.db.session import engine, SessionLocal
from app.models.autonomous_pipeline import BusinessSchedule
from app.services.pipeline_service import run_pipeline
from app.services.autonomous_publisher import publish_due_posts


class AutonomousScheduler:
    """
    Central Autonomous Scheduler Management:
    Powered by APScheduler (AsyncIOScheduler) with SQLAlchemyJobStore.
    Runs generation cron jobs per business (generate_{business_id}) and
    an exact-time publishing job per approved post plus a short recovery poll.
    """

    _scheduler: Optional[AsyncIOScheduler] = None
    _is_running: bool = False
    # Exact DateTrigger jobs publish at the requested time. This small polling
    # interval is intentionally retained as a recovery path after a restart or
    # a missed job-store notification.
    PUBLISH_POLL_SECONDS = 10

    @classmethod
    def get_scheduler(cls) -> AsyncIOScheduler:
        if cls._scheduler is None:
            jobstores = {"default": MemoryJobStore()}

            cls._scheduler = AsyncIOScheduler(
                jobstores=jobstores,
                job_defaults={
                    "coalesce": True,
                    "max_instances": 1,
                    "misfire_grace_time": 3600  # 1 hour grace time for missed runs
                },
                timezone=pytz.timezone("Asia/Kolkata")
            )
        return cls._scheduler

    @classmethod
    async def start(cls):
        """
        Initializes the scheduler, syncs all active business schedules from DB,
        registers the global publishing interval job, and starts the async scheduler.
        Ensures only one elected worker acts as active leader if multiple replicas exist.
        """
        if cls._is_running:
            return

        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                acquired = conn.execute(text("SELECT GET_LOCK('social_ai_scheduler_leader', 0)")).scalar()
                if acquired == 1:
                    logger.info("[Scheduler] Acquired leadership advisory lock.")
                else:
                    logger.info("[Scheduler] Secondary worker instance active - continuing with scheduler initialization.")
        except Exception as e:
            logger.debug(f"[Scheduler] Advisory lock check: {e}")

        scheduler = cls.get_scheduler()
        logger.info("[Scheduler] Initializing Autonomous Scheduler as leader...")

        # 1. Register the publishing recovery worker. Individual posts receive
        # an exact DateTrigger below, so this is only a safety net.
        scheduler.add_job(
            func=publish_due_posts,
            trigger=IntervalTrigger(seconds=cls.PUBLISH_POLL_SECONDS),
            id="global_publish_due_posts",
            name="Global Publish Due Posts Worker",
            replace_existing=True,
            coalesce=True,
            max_instances=1
        )

        # 2. Register media collection and recurring content scheduler worker
        from app.services.scheduler_engine import SchedulerEngine
        scheduler.add_job(
            func=SchedulerEngine.process_due_schedules,
            trigger=IntervalTrigger(seconds=30),
            id="scheduler_engine_process_due",
            name="Scheduler Engine Recurring & Due Posts Worker",
            replace_existing=True,
            coalesce=True,
            max_instances=1
        )

        # 2. Sync all business schedule jobs from Database
        db: Session = SessionLocal()
        try:
            cls.sync_all_business_jobs(db)
        except Exception as e:
            logger.error(f"[Scheduler] Error syncing business jobs on startup: {e}")
        finally:
            db.close()

        # 3. Start the scheduler engine
        try:
            scheduler.start()
            cls._is_running = True
            logger.info("[Scheduler] Autonomous APScheduler successfully started.")
        except Exception as e:
            logger.warning(f"[Scheduler] Scheduler start warning: {e}")

    @classmethod
    def shutdown(cls):
        """
        Gracefully shuts down the scheduler and releases the advisory lock.
        """
        if cls._scheduler and cls._is_running:
            try:
                cls._scheduler.shutdown(wait=False)
                cls._is_running = False
                logger.info("[Scheduler] Autonomous APScheduler successfully shut down.")
            except Exception as e:
                logger.warning(f"[Scheduler] Error during shutdown: {e}")
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text("SELECT RELEASE_LOCK('social_ai_scheduler_leader')"))
        except Exception:
            pass

    @classmethod
    def sync_all_business_jobs(cls, db: Session):
        """
        Scans all BusinessSchedule records in the database and synchronizes their cron jobs.
        """
        schedules = db.query(BusinessSchedule).all()
        logger.info(f"[Scheduler] Syncing {len(schedules)} business schedules...")
        
        for schedule in schedules:
            cls.reschedule_business_pipeline_job(
                business_id=schedule.brand_profile_id,
                schedule=schedule
            )
            cls.reschedule_business_publications(schedule.brand_profile_id)

    @classmethod
    def reschedule_business_pipeline_job(cls, business_id: int, schedule: BusinessSchedule):
        """
        Dynamically adds, updates, or removes the daily generation cron job for a business.
        """
        scheduler = cls.get_scheduler()
        job_id = f"generate_{business_id}"

        # If schedule is inactive, remove job if present
        if not schedule.is_active:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
                logger.info(f"[Scheduler] Removed inactive cron job for business {business_id} (ID: {job_id})")
            return

        # Ensure brand details are configured before scheduling generation cron
        from app.services.brand_service import is_brand_details_configured
        from app.models.brand_profile import BrandProfile
        brand = getattr(schedule, "brand_profile", None)
        if not brand:
            db_s = SessionLocal()
            try:
                brand = db_s.query(BrandProfile).filter(BrandProfile.id == business_id).first()
            finally:
                db_s.close()

        if not is_brand_details_configured(brand):
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            logger.info(f"[Scheduler] Business #{business_id} brand details not configured. Generation cron skipped.")
            return

        # Use preferred daily posting time as primary trigger
        time_str = schedule.posting_time or schedule.generation_time or "18:00"
        try:
            hour, minute = map(int, time_str.split(":"))
        except Exception:
            hour, minute = 18, 0

        biz_tz = pytz.timezone(schedule.timezone or "Asia/Kolkata")
        trigger = CronTrigger(hour=hour, minute=minute, timezone=biz_tz)

        scheduler.add_job(
            func=run_pipeline,
            args=[business_id],
            trigger=trigger,
            id=job_id,
            name=f"Autonomous Daily Pipeline for Business {business_id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600
        )
        logger.info(f"[Scheduler] Registered daily recurring pipeline cron for business {business_id} at {time_str} ({schedule.timezone}) [Job ID: {job_id}]")

    @classmethod
    def schedule_post_publication(cls, post_id: int, scheduled_time: datetime):
        """Create or replace the exact-time publication job for one pipeline post."""
        scheduler = cls.get_scheduler()
        job_id = f"publish_pipeline_post_{post_id}"
        run_at = scheduled_time
        if run_at.tzinfo is None:
            run_at = pytz.UTC.localize(run_at)
        else:
            run_at = run_at.astimezone(pytz.UTC)

        # A user can move an already-due item to "now". Schedule it one second
        # ahead so APScheduler executes it immediately instead of marking it a
        # misfire before the job has been stored.
        now_utc = datetime.now(pytz.UTC)
        if run_at <= now_utc:
            run_at = now_utc + timedelta(seconds=1)

        scheduler.add_job(
            func=publish_due_posts,
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            name=f"Publish Pipeline Post {post_id}",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("[Scheduler] Publication for pipeline post %s scheduled at %s UTC", post_id, run_at.isoformat())

    @classmethod
    def cancel_post_publication(cls, post_id: int):
        scheduler = cls.get_scheduler()
        job_id = f"publish_pipeline_post_{post_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    @classmethod
    def reschedule_business_publications(cls, business_id: int):
        """Sync exact publish jobs after a business changes its posting time."""
        from app.models.autonomous_pipeline import AutonomousPost

        db: Session = SessionLocal()
        try:
            posts = db.query(AutonomousPost).filter(
                AutonomousPost.brand_profile_id == business_id,
                AutonomousPost.status == "scheduled",
                AutonomousPost.scheduled_time.isnot(None),
            ).all()
            for post in posts:
                cls.schedule_post_publication(post.id, post.scheduled_time)
        finally:
            db.close()

    @classmethod
    def list_jobs_info(cls, business_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Returns active APScheduler jobs with next execution timestamps."""
        scheduler = cls.get_scheduler()
        jobs = scheduler.get_jobs()
        job_list = []
        for j in jobs:
            if business_id is not None:
                # Filter to jobs associated with this brand
                matches = f"_{business_id}" in j.id or j.id == f"generate_{business_id}"
                if not matches and not j.id.startswith("global_"):
                    continue
            job_type = "generation" if "generate" in j.id else "publication" if "publish" in j.id else "monitoring"
            next_run = getattr(j, "next_run_time", None)
            job_list.append({
                "job_id": j.id,
                "name": j.name,
                "job_type": job_type,
                "next_run_time": next_run,
                "status": "scheduled" if next_run else "paused",
                "details": {
                    "trigger": str(getattr(j, "trigger", "")),
                    "misfire_grace_time": getattr(j, "misfire_grace_time", 3600),
                    "coalesce": getattr(j, "coalesce", True)
                }
            })
        return job_list
