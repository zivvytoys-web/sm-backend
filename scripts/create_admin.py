"""Script to initialize super admin user."""
import sys
import os

# Set root directory in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.subscription import Subscription
from app.core.security import get_password_hash
from app.utils.enums import UserRole, SubscriptionTier, SubscriptionStatus


def create_super_admin(email: str = "admin@socialai.com", password: str = "Admin@123456"):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            print(f"[!] Super admin {email} already exists.")
            return

        org = db.query(Organization).filter(Organization.slug == "master-organization").first()
        if not org:
            org = Organization(
                name="Master Organization",
                slug="master-organization",
                description="Global administration workspace"
            )
            db.add(org)
            db.flush()

            sub = Subscription(
                organization_id=org.id,
                tier=SubscriptionTier.ENTERPRISE,
                status=SubscriptionStatus.ACTIVE,
                monthly_generation_limit=99999,
                monthly_posts_limit=99999
            )
            db.add(sub)

        admin_user = User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name="Super Administrator",
            role=UserRole.SUPER_ADMIN,
            organization_id=org.id,
            is_active=True,
            is_verified=True
        )
        db.add(admin_user)
        db.commit()
        print(f"[+] Successfully created super admin user: {email} / {password}")
    finally:
        db.close()


if __name__ == "__main__":
    create_super_admin()
