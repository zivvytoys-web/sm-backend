from enum import Enum


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    CREATOR = "creator"
    VIEWER = "viewer"
    ENTERPRISE_ADMIN = "admin"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_clean = value.strip().lower()
            if val_clean in ("superadmin", "super_admin", "super-admin"):
                return cls.SUPER_ADMIN
            if val_clean in ("admin", "enterprise_admin", "enterpriseadmin", "enterprise-admin"):
                return cls.ADMIN
            if val_clean == "manager":
                return cls.MANAGER
            if val_clean == "creator":
                return cls.CREATOR
            if val_clean == "viewer":
                return cls.VIEWER
            for member in cls:
                if member.value == val_clean or member.name.lower() == val_clean:
                    return member
        return None


class PlatformType(str, Enum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"
    TWITTER = "twitter"
    PINTEREST = "pinterest"
    SNAPCHAT = "snapchat"


    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            val_clean = value.strip().lower()
            if val_clean in ("x", "twitter"):
                return cls.TWITTER
            for member in cls:
                if member.value == val_clean or member.name.lower() == val_clean:
                    return member
        return None


class ContentStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class GenerationType(str, Enum):
    TEXT_CAPTION = "text_caption"
    TEXT_HASHTAGS = "text_hashtags"
    TEXT_ARTICLE = "text_article"
    TEXT_MARKETING = "text_marketing"
    TEXT_VARIATIONS = "text_variations"
    IMAGE_GENERATION = "image_generation"
    VIDEO_SCRIPT = "video_script"
    VIDEO_GENERATION = "video_generation"


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SubscriptionTier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


class PublishAttemptStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
