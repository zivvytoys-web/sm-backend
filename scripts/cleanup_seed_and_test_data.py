"""
Database Cleanup Script:
Removes all seed businesses, demo users, test pipeline runs, test generation jobs,
and dummy scheduled posts, while keeping the core SuperAdmin user (admin@socialai.com)
and the user's primary workspace & real connected social accounts.
"""
import sys
import os
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.subscription import Subscription
from app.models.social_account import SocialAccount
from app.models.user_organization import UserOrganization
from app.core.security import get_password_hash
from app.utils.enums import UserRole, SubscriptionTier, SubscriptionStatus

def cleanup_data():
    db = SessionLocal()
    try:
        print("[*] Starting database cleanup of seed and test data...")

        # 1. Truncate / delete test execution tables
        tables_to_clear = [
            "post_variants",
            "autonomous_posts",
            "content_briefs",
            "pipeline_runs",
            "publish_attempts",
            "scheduled_posts",
            "generated_assets",
            "generation_jobs",
            "contents",
            "media_assets",
            "usage_records",
            "collection_media",
            "collections",
            "schedule_rules",
            "apscheduler_jobs",
            "analytics",
            "audit_logs"
        ]

        db.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))

        for tbl in tables_to_clear:
            try:
                count = db.execute(text(f"DELETE FROM `{tbl}`;")).rowcount
                print(f"  [-] Cleared table `{tbl}`: {count} rows deleted.")
            except Exception as e:
                print(f"  [!] Note on table `{tbl}`: {e}")

        # Ensure schema column on analytics if missing
        try:
            from sqlalchemy import inspect
            insp = inspect(db.get_bind())
            if insp.has_table("analytics"):
                cols = {c['name'] for c in insp.get_columns("analytics")}
                if "views" not in cols:
                    db.execute(text("ALTER TABLE analytics ADD COLUMN views BIGINT NOT NULL DEFAULT 0;"))
        except Exception as e:
            print(f"  [!] Schema column check note: {e}")

        # 2. Delete test/seed users (keep ONLY superadmin@socialai.com and admin@socialai.com)
        del_count = db.execute(text("DELETE FROM users WHERE email NOT IN ('superadmin@socialai.com', 'admin@socialai.com');")).rowcount
        print(f"  [-] Deleted {del_count} test/seed users from database.")

        # 3. Ensure primary Organization (Social AI Headquarters) exists
        primary_org = db.query(Organization).filter(Organization.slug == "socialai-hq").first()
        if not primary_org:
            primary_org = db.query(Organization).first()
        if not primary_org:
            primary_org = Organization(
                name="Social AI Headquarters",
                slug="socialai-hq",
                is_active=True,
                settings={
                    "industry": "AI Marketing & Technology",
                    "description": "Enterprise Autonomous AI Social Media & Content Marketing Engine",
                    "phone": "+91 98765 43210",
                    "email": "admin@socialai.com"
                }
            )
            db.add(primary_org)
            db.flush()
        else:
            primary_org.name = "Social AI Headquarters"
            primary_org.slug = "socialai-hq"
            primary_org.is_active = True
            db.flush()

        # Delete all other seed and test organizations via direct SQL
        del_orgs_count = db.execute(text("DELETE FROM organizations WHERE id != :pid;"), {"pid": primary_org.id}).rowcount
        print(f"  [-] Deleted {del_orgs_count} seed/test organizations from database.")

        # 4. Ensure BrandProfile for primary org
        primary_bp = db.query(BrandProfile).filter(BrandProfile.organization_id == primary_org.id).first()
        if not primary_bp:
            primary_bp = BrandProfile(
                organization_id=primary_org.id,
                name=primary_org.name,
                industry="AI Marketing & Technology",
                tagline="Autonomous AI Social Growth Engine",
                location="Chennai, India",
                timings="24/7 Autonomous",
                tone_of_voice="Modern, confident, stylish, minimal, youthful, premium yet approachable.",
                target_audience="Brands, retail stores, creators",
                brand_guidelines="Clean, modern, premium",
                cta="Automate your social growth today.",
                avoid="cheap look, low contrast text, cluttered graphics",
                key_themes=["Brand Launch", "Product Drop", "Behind the Scenes"],
                hashtags=["#SocialAI", "#Automation", "#BrandGrowth"],
                color_palette=["#59171b", "#fed7b8", "#732025"]
            )
            db.add(primary_bp)
            db.flush()
        else:
            primary_bp.name = primary_org.name
            db.flush()

        # Delete all other brand profiles via direct SQL
        del_bp_count = db.execute(text("DELETE FROM brand_profiles WHERE id != :bpid;"), {"bpid": primary_bp.id}).rowcount
        print(f"  [-] Deleted {del_bp_count} seed/test brand profiles from database.")

        # 5. Clean Social Accounts: preserve real connected accounts and reassign to primary_bp
        all_socials = db.query(SocialAccount).filter(SocialAccount.is_connected == True).all()
        seen_platforms = set()
        kept_ids = []
        for s in all_socials:
            if s.platform not in seen_platforms:
                s.brand_profile_id = primary_bp.id
                seen_platforms.add(s.platform)
                kept_ids.append(s.id)
                print(f"  [+] Retained real connected social account on primary brand: {s.platform} ({s.account_name})")

        db.flush()

        # Delete duplicate or non-connected social accounts via direct SQL
        if kept_ids:
            kept_ids_str = ",".join(str(i) for i in kept_ids)
            db.execute(text(f"DELETE FROM social_accounts WHERE id NOT IN ({kept_ids_str});"))
        else:
            db.execute(text("DELETE FROM social_accounts WHERE brand_profile_id != :bpid;"), {"bpid": primary_bp.id})

        # 6. Ensure Super Admin user (superadmin@socialai.com) exists with password123
        super_admin_user = db.query(User).filter(User.email == "superadmin@socialai.com").first()
        if not super_admin_user:
            super_admin_user = User(
                email="superadmin@socialai.com",
                hashed_password=get_password_hash("password123"),
                full_name="Super Admin",
                role=UserRole.SUPER_ADMIN,
                organization_id=primary_org.id,
                is_active=True,
                is_verified=True
            )
            db.add(super_admin_user)
            db.flush()
            print("  [+] Created superadmin user: superadmin@socialai.com / password123")
        else:
            super_admin_user.hashed_password = get_password_hash("password123")
            super_admin_user.role = UserRole.SUPER_ADMIN
            super_admin_user.organization_id = primary_org.id
            super_admin_user.is_active = True
            super_admin_user.is_verified = True
            print("  [*] Updated superadmin user: superadmin@socialai.com / password123")

        # Ensure Admin user (admin@socialai.com) exists with password123
        admin_user = db.query(User).filter(User.email == "admin@socialai.com").first()
        if not admin_user:
            admin_user = User(
                email="admin@socialai.com",
                hashed_password=get_password_hash("password123"),
                full_name="Admin",
                role=UserRole.ADMIN,
                organization_id=primary_org.id,
                is_active=True,
                is_verified=True
            )
            db.add(admin_user)
            db.flush()
            print("  [+] Created admin user: admin@socialai.com / password123")
        else:
            admin_user.hashed_password = get_password_hash("password123")
            admin_user.role = UserRole.ADMIN
            admin_user.organization_id = primary_org.id
            admin_user.is_active = True
            admin_user.is_verified = True
            print("  [*] Updated admin user: admin@socialai.com / password123")

        # 7. Subscriptions: Ensure active Enterprise subscription on primary_org
        del_subs = db.query(Subscription).filter(Subscription.organization_id != primary_org.id).all()
        for sub in del_subs:
            db.delete(sub)

        primary_sub = db.query(Subscription).filter(Subscription.organization_id == primary_org.id).first()
        if not primary_sub:
            primary_sub = Subscription(
                organization_id=primary_org.id,
                tier=SubscriptionTier.ENTERPRISE,
                status=SubscriptionStatus.ACTIVE,
                monthly_generation_limit=99999,
                monthly_posts_limit=99999
            )
            db.add(primary_sub)
        else:
            primary_sub.tier = SubscriptionTier.ENTERPRISE
            primary_sub.status = SubscriptionStatus.ACTIVE
            db.add(primary_sub)

        # 8. UserOrganization mappings
        db.execute(text("DELETE FROM `user_organizations`;"))
        db.add(UserOrganization(
            user_id=super_admin_user.id,
            organization_id=primary_org.id,
            role=UserRole.SUPER_ADMIN,
            is_default=True
        ))
        db.add(UserOrganization(
            user_id=admin_user.id,
            organization_id=primary_org.id,
            role=UserRole.ADMIN,
            is_default=True
        ))

        db.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
        db.commit()
        print("[+] Database cleanup committed successfully!")

        # 9. Clean temporary media files from .media_tmp
        media_tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".media_tmp", "media")
        if os.path.exists(media_tmp_dir):
            for item in os.listdir(media_tmp_dir):
                item_path = os.path.join(media_tmp_dir, item)
                if os.path.isfile(item_path):
                    try:
                        os.remove(item_path)
                        print(f"  [-] Removed test media file: {item}")
                    except Exception as e:
                        pass
                elif os.path.isdir(item_path):
                    for sub_item in os.listdir(item_path):
                        sub_path = os.path.join(item_path, sub_item)
                        try:
                            if os.path.isfile(sub_path):
                                os.remove(sub_path)
                                print(f"  [-] Removed test media asset: {sub_item}")
                        except Exception as e:
                            pass

        print("[+] All seed data and test media cleaned successfully!")

    except Exception as e:
        db.rollback()
        print(f"[!] Cleanup failed: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_data()
