import os

path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../app/scheduler/autonomous_scheduler.py'))
with open(path, 'r', encoding='utf-8') as f:
    text = f.read().replace('\r\n', '\n')

old_start = """    @classmethod
    async def start(cls):
        \"\"\"
        Initializes the scheduler, syncs all active business schedules from DB,
        registers the global publishing interval job, and starts the async scheduler.
        \"\"\"
        if cls._is_running:
            return

        scheduler = cls.get_scheduler()
        logger.info("[Scheduler] Initializing Autonomous Scheduler...")"""

new_start = """    @classmethod
    async def start(cls):
        \"\"\"
        Initializes the scheduler, syncs all active business schedules from DB,
        registers the global publishing interval job, and starts the async scheduler.
        Ensures only one elected worker acts as active leader if multiple replicas exist.
        \"\"\"
        if cls._is_running:
            return

        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                acquired = conn.execute(text("SELECT GET_LOCK('social_ai_scheduler_leader', 0)")).scalar()
                if acquired != 1:
                    logger.info("[Scheduler] Another worker instance is currently holding the scheduler leader lock. Running in secondary monitor mode.")
                    return
        except Exception as e:
            logger.debug(f"[Scheduler] Advisory lock check: {e}")

        scheduler = cls.get_scheduler()
        logger.info("[Scheduler] Initializing Autonomous Scheduler as leader...")"""

assert old_start in text, "old_start not found"
text = text.replace(old_start, new_start)

# Update shutdown with release lock
old_shutdown = """    @classmethod
    def shutdown(cls):
        \"\"\"
        Gracefully shuts down the scheduler.
        \"\"\"
        if cls._scheduler and cls._is_running:
            try:
                cls._scheduler.shutdown(wait=False)
                cls._is_running = False
                logger.info("[Scheduler] Autonomous APScheduler successfully shut down.")
            except Exception as e:
                logger.warning(f"[Scheduler] Error during shutdown: {e}")"""

new_shutdown = """    @classmethod
    def shutdown(cls):
        \"\"\"
        Gracefully shuts down the scheduler and releases the advisory lock.
        \"\"\"
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
            pass"""

assert old_shutdown in text, "old_shutdown not found"
text = text.replace(old_shutdown, new_shutdown)

# Update reschedule_business_pipeline_job to handle generation_date
old_resched = """        biz_tz = pytz.timezone(schedule.timezone or "Asia/Kolkata")
        trigger = CronTrigger(hour=hour, minute=minute, timezone=biz_tz)

        scheduler.add_job(
            func=run_pipeline,
            args=[business_id],
            trigger=trigger,
            id=job_id,
            name=f"Daily Content Generation for Business {business_id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600
        )"""

new_resched = """        biz_tz = pytz.timezone(schedule.timezone or "Asia/Kolkata")
        gen_date_str = getattr(schedule, "generation_date", None)
        trigger = None
        if gen_date_str:
            try:
                gen_date = datetime.strptime(gen_date_str, "%Y-%m-%d").date()
                target_dt = biz_tz.localize(datetime(gen_date.year, gen_date.month, gen_date.day, hour, minute, 0))
                now_tz = datetime.now(biz_tz)
                if target_dt > now_tz:
                    trigger = DateTrigger(run_date=target_dt.astimezone(pytz.UTC))
                    logger.info(f"[Scheduler] Setting exact date trigger for business {business_id} at {target_dt.isoformat()}")
            except Exception as e:
                logger.warning(f"[Scheduler] Error parsing generation_date '{gen_date_str}': {e}")

        if trigger is None:
            trigger = CronTrigger(hour=hour, minute=minute, timezone=biz_tz)

        scheduler.add_job(
            func=run_pipeline,
            args=[business_id],
            trigger=trigger,
            id=job_id,
            name=f"Autonomous Content Generation for Business {business_id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600
        )"""

assert old_resched in text, "old_resched not found"
text = text.replace(old_resched, new_resched)

# Add list_jobs_info at the end of class
old_end = """    @classmethod
    def reschedule_business_publications(cls, business_id: int):
        \"\"\"Sync exact publish jobs after a business changes its posting time.\"\"\"
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
            db.close()"""

new_end = """    @classmethod
    def reschedule_business_publications(cls, business_id: int):
        \"\"\"Sync exact publish jobs after a business changes its posting time.\"\"\"
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
        \"\"\"Returns active APScheduler jobs with next execution timestamps.\"\"\"
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
            job_list.append({
                "job_id": j.id,
                "name": j.name,
                "job_type": job_type,
                "next_run_time": j.next_run_time,
                "status": "scheduled" if j.next_run_time else "paused",
                "details": {
                    "trigger": str(j.trigger),
                    "misfire_grace_time": getattr(j, "misfire_grace_time", 3600),
                    "coalesce": getattr(j, "coalesce", True)
                }
            })
        return job_list"""

assert old_end in text, "old_end not found"
text = text.replace(old_end, new_end)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated app/scheduler/autonomous_scheduler.py successfully!")
