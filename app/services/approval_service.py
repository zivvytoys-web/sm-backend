from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.approval import Approval
from app.models.content import Content
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.schemas.approval import ApprovalCreate, ApprovalReview
from app.utils.enums import ApprovalStatus, ContentStatus


class ApprovalService:
    @staticmethod
    def list_approvals(db: Session, organization_id: int, status_filter: Optional[ApprovalStatus] = None) -> List[Approval]:
        query = db.query(Approval).join(Content, Approval.content_id == Content.id).join(
            BrandProfile, Content.brand_profile_id == BrandProfile.id
        ).filter(BrandProfile.organization_id == organization_id)
        if status_filter:
            query = query.filter(Approval.status == status_filter)
        return query.order_by(Approval.created_at.desc()).all()

    @staticmethod
    def request_approval(db: Session, requester: User, payload: ApprovalCreate) -> Approval:
        content = db.query(Content).join(BrandProfile, Content.brand_profile_id == BrandProfile.id).filter(
            Content.id == payload.content_id,
            BrandProfile.organization_id == requester.organization_id
        ).first()
        if not content:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found or unauthorized.")

        content.status = ContentStatus.PENDING_APPROVAL

        approval = Approval(
            content_id=payload.content_id,
            requester_id=requester.id,
            comment=payload.comment,
            status=ApprovalStatus.PENDING
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return approval

    @staticmethod
    def review_approval(db: Session, approval_id: int, reviewer: User, payload: ApprovalReview) -> Approval:
        approval = db.query(Approval).join(Content, Approval.content_id == Content.id).join(
            BrandProfile, Content.brand_profile_id == BrandProfile.id
        ).filter(
            Approval.id == approval_id,
            BrandProfile.organization_id == reviewer.organization_id
        ).first()
        if not approval:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found or unauthorized.")

        approval.status = payload.status
        approval.comment = payload.comment or approval.comment
        approval.reviewer_id = reviewer.id
        approval.reviewed_at = datetime.utcnow()

        content = db.query(Content).filter(Content.id == approval.content_id).first()
        if content:
            if payload.status == ApprovalStatus.APPROVED:
                content.status = ContentStatus.APPROVED
            elif payload.status == ApprovalStatus.REJECTED:
                content.status = ContentStatus.REJECTED

        db.commit()
        db.refresh(approval)
        return approval
