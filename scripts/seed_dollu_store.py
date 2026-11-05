import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.subscription import Subscription
from app.models.content import Content
from app.models.campaign import Campaign
from app.models.social_account import SocialAccount
from app.core.security import get_password_hash
from app.utils.enums import UserRole, SubscriptionTier, SubscriptionStatus, ContentStatus, PlatformType


def seed_dollu():
    db = SessionLocal()
    try:
        # 1. Organization
        org = db.query(Organization).filter(Organization.name == "DOLLU Men's Fashion (Chennai)").first()
        if not org:
            org = Organization(
                name="DOLLU Men's Fashion (Chennai)",
                slug="dollu-mens-fashion-chennai",
                description="Modern men's fashion apparel retail store in Chennai"
            )
            db.add(org)
            db.flush()

        # 2. Subscription
        sub = db.query(Subscription).filter(Subscription.organization_id == org.id).first()
        if not sub:
            sub = Subscription(
                organization_id=org.id,
                tier=SubscriptionTier.PRO,
                status=SubscriptionStatus.ACTIVE,
                monthly_generation_limit=500,
                monthly_posts_limit=300
            )
            db.add(sub)

        # 3. User
        user = db.query(User).filter(User.email == "admin@dollu-store.example.com").first()
        if not user:
            user = User(
                email="admin@dollu-store.example.com",
                hashed_password=get_password_hash("dolluFashion2026!"),
                full_name="DOLLU Store Admin",
                role=UserRole.ADMIN,
                organization_id=org.id,
                is_active=True,
                is_verified=True
            )
            db.add(user)
            db.flush()
        else:
            user.hashed_password = get_password_hash("dolluFashion2026!")
            user.organization_id = org.id
            db.add(user)

        # 4. Brand Profile
        brand = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
        if not brand:
            brand = BrandProfile(
                organization_id=org.id,
                name="DOLLU",
                industry="Men's Fashion & Apparel Retail",
                tagline="Own Your Style.",
                location="Chennai, Tamil Nadu, India",
                timings="Monday – Sunday: 10:00 AM – 9:00 PM",
                tone_of_voice="Modern, confident, stylish, minimal, youthful, premium yet approachable.",
                target_audience="Men aged approximately 18–40 in Chennai interested in modern, comfortable clothing",
                brand_guidelines="DOLLU is a modern men's fashion store offering stylish casual wear, formal clothing, trendy shirts, T-shirts, jeans, trousers, jackets, ethnic wear, and accessories in Chennai.",
                cta="Visit DOLLU in Chennai or DM to order. Own your style.",
                avoid="cheap, low quality, women's dresses, outdated cuts, heavy polyester",
                key_themes=["breathable linen", "tailored cuts", "Chennai weather", "stretch denim", "sharp silhouettes", "premium cotton"],
                hashtags=["#DOLLU", "#MensFashion", "#ChennaiFashion", "#OutfitInspiration", "#NewArrivals"],
                color_palette=["#0F172A", "#C7A45A", "#0D9488", "#F6F8FA"],
                meta_info={
                    "phone": "+91 98401 22345",
                    "email": "contact@dollufashion.com",
                    "gstin": "33AAACD1234F1Z8",
                    "flagshipAddr": "Shop No. 14, 1st Avenue, Anna Nagar East, Chennai, Tamil Nadu 600102",
                    "branchAddr": "South Usman Road, T. Nagar, Chennai, Tamil Nadu 600017",
                    "categories": "Linen Shirts, Formal Blazers, Stretch Denim, Chinos, Festive Ethnic Wear, Belts & Accessories"
                }
            )
            db.add(brand)
            db.flush()

        # 5. Connected Social Accounts
        accounts = [
            (PlatformType.INSTAGRAM, "@dollu.fashion.demo", "ig_dollu_101"),
            (PlatformType.FACEBOOK, "DOLLU Men's Fashion Chennai", "fb_dollu_202"),
            (PlatformType.YOUTUBE, "@DOLLUFashionDemo", "yt_dollu_303"),
            (PlatformType.LINKEDIN, "DOLLU Fashion Retail", "li_dollu_404")
        ]
        for platform, name, acc_id in accounts:
            existing = db.query(SocialAccount).filter(SocialAccount.brand_profile_id == brand.id, SocialAccount.platform == platform).first()
            if not existing:
                sa = SocialAccount(
                    brand_profile_id=brand.id,
                    platform=platform,
                    account_name=name,
                    account_id=acc_id,
                    is_connected=True,
                    profile_url=f"https://{platform.value}.com/{name.strip('@')}",
                    access_token="mock_valid_token_xyz"
                )
                db.add(sa)

        # 6. Campaigns
        camp = db.query(Campaign).filter(Campaign.organization_id == org.id).first()
        if not camp:
            camp = Campaign(
                organization_id=org.id,
                brand_profile_id=brand.id,
                title="DOLLU New Season Drop",
                objective="Promote breathable casual linen & denim collections in Chennai",
                tags=["Instagram", "Facebook", "YouTube"],
                status="Active"
            )
            db.add(camp)
            db.flush()

        # 7. Initial Content in MySQL
        content_count = db.query(Content).filter(Content.brand_profile_id == brand.id).count()
        if content_count == 0:
            posts = [
                {
                    "title": "Summer Linen Shirts Drop",
                    "caption": "LEVEL UP YOUR STYLE WITH DOLLU. ✨\n\nFrom everyday essentials to sharp statement looks, find styles that match your personality.\n\nExplore our new-season breathable linen shirts, tailored stretch denim, and smart-casual layers.\n\n📍 Visit DOLLU Store in Chennai (10 AM to 9 PM) or DM us for orders.\n\n#DOLLU #MensFashion #MensStyle #ChennaiFashion #OutfitInspiration #NewArrivals",
                    "hashtags": ["#DOLLU", "#MensFashion", "#MensStyle", "#ChennaiFashion", "#OutfitInspiration", "#NewArrivals"],
                    "target_platforms": [PlatformType.INSTAGRAM],
                    "status": ContentStatus.PENDING_APPROVAL,
                    "meta_info": {
                        "format": "Image post",
                        "contentType": "image",
                        "creativeDirection": "Warm studio lighting on breathable linen shirts",
                        "cta": "Visit DOLLU store in Chennai or DM for orders.",
                        "priceTag": "₹1,299",
                        "mediaUrl": "generated_art_linen_shirt",
                        "thumbnailUrl": "generated_art_linen_shirt",
                        "icon": "👔"
                    }
                },
                {
                    "title": "3-Way Styling Reel: Casual to Smart Casual",
                    "caption": "3 WEEKEND OUTFIT FORMULAS ⚡\n\nFormula 1: Linen Shirt + Tailored Chinos\nFormula 2: Denim Layered + Knit Tee\nFormula 3: Sharp Blazer + Dark Denim\n\nSave this reel for your Saturday outfit planning! 📌\n\n#DOLLU #StyleReel #WeekendOutfits #ChennaiMen #FashionGuide",
                    "hashtags": ["#DOLLU", "#StyleReel", "#WeekendOutfits", "#ChennaiMen", "#FashionGuide"],
                    "target_platforms": [PlatformType.INSTAGRAM],
                    "status": ContentStatus.APPROVED,
                    "meta_info": {
                        "format": "Reel / Short Video",
                        "contentType": "video",
                        "creativeDirection": "Fast outfit transitions with upbeat music",
                        "cta": "Save this reel for your weekend outfit inspo.",
                        "priceTag": "₹1,499",
                        "mediaUrl": "video_reel_casual_transitions",
                        "thumbnailUrl": "video_reel_casual_transitions",
                        "icon": "🎬",
                        "duration": "0:30"
                    }
                },
                {
                    "title": "Weekend Store Experience & Tailored Fits",
                    "caption": "UPGRADE YOUR WARDROBE THIS WEEKEND 🛍️\n\nLooking for the perfect fit? Visit DOLLU in Chennai for curated men's shirts, trousers, denim, and smart-casual layers.\n\n✔️ Custom alterations available on spot\n✔️ New festive arrivals in stock\n\n📍 DOLLU Men's Fashion Store, Chennai\n⏰ Open Daily: 10:00 AM – 9:00 PM\n\n#DOLLU #ChennaiShopping #MensFashionStore #WeekendStyle",
                    "hashtags": ["#DOLLU", "#ChennaiShopping", "#MensFashionStore", "#WeekendStyle"],
                    "target_platforms": [PlatformType.FACEBOOK],
                    "status": ContentStatus.SCHEDULED,
                    "meta_info": {
                        "format": "Text Hook & Announcement",
                        "contentType": "text",
                        "creativeDirection": "Clean text announcement with emojis",
                        "cta": "Visit us this weekend at DOLLU Chennai Store.",
                        "priceTag": "Store Special",
                        "icon": "🏬",
                        "scheduledDate": "2026-10-10",
                        "scheduledTime": "18:00",
                        "scheduledAccount": "@dollu.fashion.demo (Instagram)"
                    }
                }
            ]
            for p in posts:
                c = Content(
                    brand_profile_id=brand.id,
                    campaign_id=camp.id,
                    creator_id=user.id,
                    title=p["title"],
                    caption=p["caption"],
                    hashtags=p["hashtags"],
                    target_platforms=p["target_platforms"],
                    status=p["status"],
                    meta_info=p["meta_info"]
                )
                db.add(c)

        db.commit()
        print(f"[+] Successfully seeded DOLLU brand, admin user, social accounts, and content in MySQL!")
    finally:
        db.close()


if __name__ == "__main__":
    seed_dollu()
