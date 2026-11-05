from datetime import datetime, date
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.usage_record import UsageRecord
from app.models.subscription import Subscription
from app.utils.exceptions import QuotaExceededException


class UsageService:
    @staticmethod
    def record_usage(db: Session, organization_id: int, metric_name: str, amount: int = 1):
        today = date.today()
        period = today.strftime("%Y-%m")

        record = UsageRecord(
            organization_id=organization_id,
            metric_name=metric_name,
            amount=amount,
            billing_period=period,
            date=today
        )
        db.add(record)
        db.commit()

    @staticmethod
    def check_quota(db: Session, organization_id: int, metric_name: str) -> bool:
        today = date.today()
        period = today.strftime("%Y-%m")

        total_used = db.query(func.sum(UsageRecord.amount)).filter(
            UsageRecord.organization_id == organization_id,
            UsageRecord.metric_name == metric_name,
            UsageRecord.billing_period == period
        ).scalar() or 0

        sub = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        limit = sub.monthly_generation_limit if (sub and sub.monthly_generation_limit is not None) else 1000

        if total_used >= limit:
            raise QuotaExceededException(
                f"Monthly usage quota ({limit}) for {metric_name} exceeded. Current usage: {total_used}."
            )
        return True
