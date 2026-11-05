"""Script to populate rich seed data for local testing and frontend development."""
import sys
import os
from datetime import datetime, timedelta, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.models.organization import Organization
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.models.subscription import Subscription
from app.models.campaign import Campaign
from app.models.content import Content
from app.models.media_asset import MediaAsset
from app.models.scheduled_post import ScheduledPost
from app.models.analytics import Analytics
from app.core.security import get_password_hash
from app.utils.enums import UserRole, SubscriptionTier, SubscriptionStatus, PlatformType, ContentStatus, MediaType


def seed_data():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Check if already seeded
        if db.query(Organization).filter(Organization.slug == "hypergrowth-media").first():
            print("[*] Sample data already exists.")
            return

        print("[*] Seeding sample organization, users, brands, and content...")
        
        # 1. Organization
        org = Organization(
            name="HyperGrowth Media Inc.",
            slug="hypergrowth-media",
            description="AI-driven digital brand studio",
            settings={"theme": "dark", "timezone": "UTC"}
        )
        db.add(org)
        db.flush()

        # 2. Subscription
        sub = Subscription(
            organization_id=org.id,
            tier=SubscriptionTier.PRO,
            status=SubscriptionStatus.ACTIVE,
            monthly_generation_limit=2000,
            monthly_posts_limit=5000
        )
        db.add(sub)

        # 3. Users
        creator = User(
            email="creator@hypergrowth.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="Alex River",
            role=UserRole.CREATOR,
            organization_id=org.id,
            is_active=True,
            is_verified=True
        )
        manager = User(
            email="manager@hypergrowth.com",
            hashed_password=get_password_hash("Password123!"),
            full_name="Sarah Connor",
            role=UserRole.MANAGER,
            organization_id=org.id,
            is_active=True,
            is_verified=True
        )
        db.add_all([creator, manager])
        db.flush()

        # 4. Brand Profile
        brand = BrandProfile(
            organization_id=org.id,
            name="Apex Techwear",
            industry="Fashion & Technology",
            tone_of_voice="Futuristic, Minimalist, Inspiring",
            target_audience="Tech workers, designers, urban digital nomads aged 22-40",
            brand_guidelines="Use sleek monochrome aesthetics with neon accents. Keep copy punchy.",
            key_themes=["Cyberpunk Aesthetics", "Urban Mobility", "Sustainable Performance"],
            hashtags=["#ApexTech", "#FutureWear", "#CyberFashion", "#TechwearStyle"],
            color_palette=["#0F172A", "#3B82F6", "#10B981", "#6366F1"]
        )
        db.add(brand)
        db.flush()

        # 5. Connected Social Accounts
        ig_acc = SocialAccount(
            platform=PlatformType.INSTAGRAM,
            account_name="@apextechwear",
            account_id="ig_984729104",
            profile_url="https://instagram.com/apextechwear",
            access_token="mock_ig_token",
            brand_profile_id=brand.id
        )
        li_acc = SocialAccount(
            platform=PlatformType.LINKEDIN,
            account_name="Apex Techwear Official",
            account_id="li_87239102",
            profile_url="https://linkedin.com/company/apex-techwear",
            access_token="mock_li_token",
            brand_profile_id=brand.id
        )
        db.add_all([ig_acc, li_acc])
        db.flush()

        # 6. Campaign
        campaign = Campaign(
            organization_id=org.id,
            brand_profile_id=brand.id,
            title="Autumn Cyber-Drop 2026",
            objective="Drive awareness and pre-orders for the Quantum Shell Jacket",
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=30),
            tags=["Product Launch", "Drop 04", "Q4"],
            status="active"
        )
        db.add(campaign)
        db.flush()

        # 7. Media Asset
        asset = MediaAsset(
            organization_id=org.id,
            file_name="quantum_shell_jacket.jpg",
            file_url="https://images.unsplash.com/photo-1551028719-00167b16eac5?auto=format&fit=crop&w=1000&q=80",
            mime_type="image/jpeg",
            media_type=MediaType.IMAGE,
            file_size_bytes=1048576
        )
        db.add(asset)
        db.flush()

        # 8. Content Post
        post_content = Content(
            title="Quantum Shell Reveal",
            caption="Engineered for extremes. The Quantum Shell Jacket combines weather-shielding nanomaterials with adaptive urban aesthetics. Pre-orders live now. ⚡️",
            hashtags=["#ApexTech", "#QuantumShell", "#Techwear"],
            target_platforms=[PlatformType.INSTAGRAM, PlatformType.LINKEDIN],
            status=ContentStatus.SCHEDULED,
            brand_profile_id=brand.id,
            campaign_id=campaign.id,
            creator_id=creator.id,
            media_asset_id=asset.id
        )
        db.add(post_content)
        db.flush()

        # 9. Scheduled Post
        scheduled = ScheduledPost(
            content_id=post_content.id,
            social_account_id=ig_acc.id,
            scheduled_time=datetime.utcnow() + timedelta(hours=4),
            status=ContentStatus.SCHEDULED
        )
        db.add(scheduled)

        # 10. Analytics records for historical chart
        for i in range(14):
            day = date.today() - timedelta(days=i)
            analytics_entry = Analytics(
                social_account_id=ig_acc.id,
                date=day,
                impressions=1200 + (i * 85),
                reach=980 + (i * 60),
                engagements=180 + (i * 15),
                likes=140 + (i * 12),
                comments=22 + (i * 2),
                shares=18 + i,
                clicks=40 + (i * 3),
                engagement_rate=0.142
            )
            db.add(analytics_entry)

        db.commit()
        print("[+] Successfully seeded rich development data!")
    finally:
        db.close()


if __name__ == "__main__":
    seed_data()
