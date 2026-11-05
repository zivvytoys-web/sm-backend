import random
from datetime import datetime, date, time, timedelta
from typing import List, Optional, Dict, Any
import pytz
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import HTTPException, status

from app.models.schedule_rule import ScheduleRule
from app.models.scheduled_post import ScheduledPost
from app.models.collection import Collection
from app.models.media_asset import MediaAsset
from app.models.social_account import SocialAccount
from app.models.user import User
from app.schemas.scheduling_rule import ScheduleRuleCreate, ScheduleRuleUpdate
from app.core.logging import logger
from app.scheduler.publishing_worker import PublishingWorker


WEEKDAY_MAP = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun"
}


class SchedulerEngine:
    @staticmethod
    def get_timezone(tz_name: Optional[str]) -> Any:
        try:
            return pytz.timezone(tz_name or "Asia/Kolkata")
        except Exception:
            return pytz.timezone("Asia/Kolkata")

    @classmethod
    def calculate_upcoming_slots(
        cls,
        rule: ScheduleRule,
        from_utc: Optional[datetime] = None,
        max_slots: int = 5
    ) -> List[datetime]:
        """
        Calculates the next eligible publishing slot timestamps (in UTC)
        strictly adhering to timezone, intervals, weekdays, and publishing window.
        """
        tz = cls.get_timezone(rule.timezone)
        now_utc = from_utc or datetime.utcnow()
        if now_utc.tzinfo is None:
            now_utc = pytz.utc.localize(now_utc)
        now_tz = now_utc.astimezone(tz)

        # Parse start and end times for the publishing window
        try:
            st_parts = [int(p) for p in (rule.start_time or "09:00").split(":")]
            st_time = time(st_parts[0], st_parts[1])
        except Exception:
            st_time = time(9, 0)

        try:
            et_parts = [int(p) for p in (rule.end_time or "19:00").split(":")]
            et_time = time(et_parts[0], et_parts[1])
        except Exception:
            et_time = time(19, 0)

        # Earliest candidate date
        current_date = now_tz.date()
        if rule.start_date:
            try:
                sd = datetime.strptime(rule.start_date, "%Y-%m-%d").date()
                if sd > current_date:
                    current_date = sd
            except Exception:
                pass

        end_date_limit = None
        if rule.end_date:
            try:
                end_date_limit = datetime.strptime(rule.end_date, "%Y-%m-%d").date()
            except Exception:
                pass

        weekdays_filter = [w.lower()[:3] for w in (rule.weekdays or [])]
        interval = max(1, rule.recurrence_interval or 1)
        recurrence_type = (rule.recurrence_type or "daily").lower()

        slots: List[datetime] = []
        days_checked = 0
        max_days = 90  # Guard against runaway loops

        while len(slots) < max_slots and days_checked < max_days:
            days_checked += 1
            if end_date_limit and current_date > end_date_limit:
                break

            day_code = WEEKDAY_MAP.get(current_date.weekday())

            # Weekday validations
            is_valid_day = True
            if recurrence_type == "weekdays" and current_date.weekday() >= 5:  # Sat/Sun
                is_valid_day = False
            elif recurrence_type == "custom_weekly" and weekdays_filter and day_code not in weekdays_filter:
                is_valid_day = False

            if is_valid_day:
                if recurrence_type == "hourly":
                    # Generate slots across the active window
                    curr_time = st_time
                    while curr_time <= et_time and len(slots) < max_slots:
                        candidate_dt = tz.localize(datetime.combine(current_date, curr_time))
                        if candidate_dt > now_tz:
                            slots.append(candidate_dt.astimezone(pytz.utc).replace(tzinfo=None))
                        # Step by recurrence_interval hours
                        curr_hour = curr_time.hour + interval
                        if curr_hour > 23:
                            break
                        curr_time = time(curr_hour, curr_time.minute)
                else:
                    # Daily, every_n_days, weekdays, custom_weekly, once
                    candidate_dt = tz.localize(datetime.combine(current_date, st_time))
                    if candidate_dt > now_tz:
                        slots.append(candidate_dt.astimezone(pytz.utc).replace(tzinfo=None))

            # Advance current_date
            if recurrence_type == "every_n_days":
                current_date += timedelta(days=interval)
            else:
                current_date += timedelta(days=1)

        return slots

    @classmethod
    def create_rule(
        cls,
        db: Session,
        user: User,
        data: ScheduleRuleCreate
    ) -> ScheduleRule:
        """Creates a schedule rule and computes its initial next_run_at timestamp."""
        if not user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User must belong to an organization."
            )

        rule = ScheduleRule(
            name=data.name.strip(),
            organization_id=user.organization_id,
            user_id=user.id,
            collection_id=data.collection_id,
            social_account_id=data.social_account_id,
            platform=data.platform,
            recurrence_type=data.recurrence_type,
            recurrence_interval=data.recurrence_interval,
            weekdays=data.weekdays,
            start_date=data.start_date,
            end_date=data.end_date,
            start_time=data.start_time,
            end_time=data.end_time,
            timezone=data.timezone,
            max_posts_per_day=data.max_posts_per_day,
            selection_strategy=data.selection_strategy,
            on_exhaustion=data.on_exhaustion,
            status=data.status or "active",
            meta_info=data.meta_info
        )

        upcoming = cls.calculate_upcoming_slots(rule, max_slots=1)
        rule.next_run_at = upcoming[0] if upcoming else None

        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    @classmethod
    def update_rule(
        cls,
        db: Session,
        rule_id: int,
        organization_id: int,
        data: ScheduleRuleUpdate
    ) -> ScheduleRule:
        """Updates a schedule rule and recalculates next_run_at."""
        rule = db.query(ScheduleRule).filter(
            ScheduleRule.id == rule_id,
            ScheduleRule.organization_id == organization_id
        ).first()

        if not rule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Schedule rule not found."
            )

        update_dict = data.dict(exclude_unset=True)
        for k, v in update_dict.items():
            if v is not None:
                setattr(rule, k, v)

        if rule.status == "active":
            upcoming = cls.calculate_upcoming_slots(rule, max_slots=1)
            rule.next_run_at = upcoming[0] if upcoming else None

        db.commit()
        db.refresh(rule)
        return rule

    @classmethod
    def pause_rule(cls, db: Session, rule_id: int, organization_id: int) -> ScheduleRule:
        rule = db.query(ScheduleRule).filter(
            ScheduleRule.id == rule_id,
            ScheduleRule.organization_id == organization_id
        ).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Schedule rule not found.")
        rule.status = "paused"
        db.commit()
        db.refresh(rule)
        return rule

    @classmethod
    def resume_rule(cls, db: Session, rule_id: int, organization_id: int) -> ScheduleRule:
        rule = db.query(ScheduleRule).filter(
            ScheduleRule.id == rule_id,
            ScheduleRule.organization_id == organization_id
        ).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Schedule rule not found.")
        rule.status = "active"
        upcoming = cls.calculate_upcoming_slots(rule, max_slots=1)
        rule.next_run_at = upcoming[0] if upcoming else None
        db.commit()
        db.refresh(rule)
        return rule

    @classmethod
    def cancel_rule(cls, db: Session, rule_id: int, organization_id: int) -> ScheduleRule:
        rule = db.query(ScheduleRule).filter(
            ScheduleRule.id == rule_id,
            ScheduleRule.organization_id == organization_id
        ).first()
        if not rule:
            raise HTTPException(status_code=404, detail="Schedule rule not found.")
        rule.status = "cancelled"
        rule.next_run_at = None
        db.commit()
        db.refresh(rule)
        return rule

    @classmethod
    def select_next_media(
        cls,
        db: Session,
        rule: ScheduleRule
    ) -> Optional[MediaAsset]:
        """
        Selects next media asset according to the rule's selection strategy
        (order, oldest_first, newest_first, random) avoiding duplicates unless reuse is permitted.
        """
        if not rule.collection_id:
            return None

        collection = db.query(Collection).filter(Collection.id == rule.collection_id).first()
        if not collection or not collection.media_assets:
            return None

        # Fetch IDs of media assets already scheduled or published under this rule
        published_asset_ids = {
            p.media_asset_id
            for p in db.query(ScheduledPost.media_asset_id).filter(
                ScheduledPost.schedule_rule_id == rule.id,
                ScheduledPost.media_asset_id.isnot(None),
                ScheduledPost.status.in_(["scheduled", "publishing", "published"])
            ).all()
        }

        # Filter available assets
        available_assets = [m for m in collection.media_assets if m.id not in published_asset_ids]

        # If exhausted
        if not available_assets:
            return None

        strategy = (rule.selection_strategy or "order").lower()
        if strategy == "oldest_first":
            available_assets.sort(key=lambda m: m.created_at)
            return available_assets[0]
        elif strategy == "newest_first":
            available_assets.sort(key=lambda m: m.created_at, reverse=True)
            return available_assets[0]
        elif strategy == "random":
            return random.choice(available_assets)
        else:  # "order" default
            return available_assets[0]

    @classmethod
    async def process_recurring_rules(cls, db: Session) -> int:
        """
        Checks active schedule rules whose next_run_at has arrived,
        selects eligible collection media, generates the ScheduledPost,
        and advances rule.next_run_at.
        """
        now = datetime.utcnow()
        active_rules = db.query(ScheduleRule).filter(
            ScheduleRule.status == "active",
            ScheduleRule.next_run_at.isnot(None),
            ScheduleRule.next_run_at <= now
        ).all()

        dispatched_count = 0
        for rule in active_rules:
            # Check daily post count limit in target timezone
            tz = cls.get_timezone(rule.timezone)
            now_tz = pytz.utc.localize(now).astimezone(tz)
            start_of_day_tz = tz.localize(datetime.combine(now_tz.date(), time(0, 0)))
            start_of_day_utc = start_of_day_tz.astimezone(pytz.utc).replace(tzinfo=None)

            posts_today = db.query(func.count(ScheduledPost.id)).filter(
                ScheduledPost.schedule_rule_id == rule.id,
                ScheduledPost.scheduled_time >= start_of_day_utc
            ).scalar() or 0

            if posts_today >= rule.max_posts_per_day:
                logger.info(f"Rule #{rule.id} reached daily cap ({rule.max_posts_per_day}). Advancing slot...")
                upcoming = cls.calculate_upcoming_slots(rule, from_utc=now + timedelta(hours=12), max_slots=1)
                rule.next_run_at = upcoming[0] if upcoming else None
                db.commit()
                continue

            # Select media
            media = cls.select_next_media(db, rule)
            if not media:
                logger.warning(f"Rule #{rule.id} collection #{rule.collection_id} exhausted.")
                if rule.on_exhaustion == "pause":
                    rule.status = "paused"
                    rule.next_run_at = None
                elif rule.on_exhaustion == "skip":
                    upcoming = cls.calculate_upcoming_slots(rule, from_utc=now + timedelta(minutes=30), max_slots=1)
                    rule.next_run_at = upcoming[0] if upcoming else None
                else:  # "wait"
                    rule.next_run_at = now + timedelta(hours=1)
                db.commit()
                continue

            # Create scheduled post
            post_title = media.title or f"{rule.name} - Scheduled Post"
            post_caption = media.description or media.title or ""
            post_format = "reel" if (hasattr(media.media_type, "value") and media.media_type.value == "video") else "post"

            scheduled_post = ScheduledPost(
                title=post_title,
                caption=post_caption,
                hashtags=[],
                internal_tags=media.tags or [],
                platform=rule.platform,
                post_format=post_format,
                scheduled_time=rule.next_run_at,
                timezone=rule.timezone,
                status="scheduled",
                social_account_id=rule.social_account_id,
                media_asset_id=media.id,
                schedule_rule_id=rule.id,
                publish_metadata={"created_by_rule": rule.id, "rule_name": rule.name}
            )

            db.add(scheduled_post)

            # Advance rule next_run_at
            upcoming = cls.calculate_upcoming_slots(rule, from_utc=now + timedelta(minutes=5), max_slots=1)
            rule.next_run_at = upcoming[0] if upcoming else None

            db.commit()
            dispatched_count += 1
            logger.info(f"Generated scheduled post #{scheduled_post.id} from rule #{rule.id} for media #{media.id}")

        return dispatched_count

    @classmethod
    async def process_due_schedules(cls):
        """
        Master periodic scheduler tick:
        1. Evaluates recurring schedule rules.
        2. Publishes due scheduled posts.
        """
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            # 1. Generate posts from recurring rules
            await cls.process_recurring_rules(db)
            # 2. Publish due posts
            await PublishingWorker.process_due_posts()
        except Exception as e:
            logger.error(f"[SchedulerEngine] Error during scheduled processing: {e}")
        finally:
            db.close()
