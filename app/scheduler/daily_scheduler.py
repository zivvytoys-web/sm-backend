from datetime import datetime, date
from typing import List
from sqlalchemy.orm import Session
from app.models.scheduled_post import ScheduledPost


class DailyScheduler:
    @staticmethod
    def get_day_queue(db: Session, target_date: date) -> List[ScheduledPost]:
        start_of_day = datetime.combine(target_date, datetime.min.time())
        end_of_day = datetime.combine(target_date, datetime.max.time())

        return db.query(ScheduledPost).filter(
            ScheduledPost.scheduled_time >= start_of_day,
            ScheduledPost.scheduled_time <= end_of_day
        ).order_by(ScheduledPost.scheduled_time.asc()).all()
