from datetime import datetime, timedelta
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from app.models.scheduled_post import ScheduledPost
from app.models.content import Content
from app.utils.enums import ContentStatus


class ScheduleManager:
    """Manages optimal posting schedules and slot allocation."""

    RECOMMENDED_PEAK_HOURS = {
        "instagram": [9, 12, 17, 20],
        "linkedin": [8, 10, 13, 16],
        "facebook": [9, 13, 15],
        "youtube": [14, 16, 18],
    }

    @classmethod
    def get_optimal_slots(cls, platform: str, start_date: datetime, count: int = 7) -> List[datetime]:
        hours = cls.RECOMMENDED_PEAK_HOURS.get(platform.lower(), [10, 14, 18])
        slots = []
        current = start_date

        while len(slots) < count:
            for h in hours:
                slot = current.replace(hour=h, minute=0, second=0, microsecond=0)
                if slot > start_date:
                    slots.append(slot)
                if len(slots) >= count:
                    break
            current += timedelta(days=1)

        return slots
