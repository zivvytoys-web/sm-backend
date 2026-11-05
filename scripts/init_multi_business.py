import os
import sys

# Ensure backend root is in python path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy.orm import Session
from app.db.session import engine, SessionLocal
from app.db.base import Base
import app.models  # load all models
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.user import User
from app.models.user_organization import UserOrganization
from app.models.subscription import Subscription
from app.core.security import get_password_hash
from app.utils.enums import UserRole, SubscriptionTier, SubscriptionStatus

PREDEFINED_BUSINESSES = [
    {
        "name": "aminoid Fashion & Apparel Studio",
        "slug": "aminoid-fashion",
        "description": "Modern luxury fashion & retail studio specializing in breathable linens, formal blazers, and curated casual wear.",
        "logo_url": "https://images.unsplash.com/photo-1441986300917-64674bd600d8?auto=format&fit=crop&w=400&q=80",
        "settings": {
            "industry": "Fashion & Apparel Retail",
            "category": "Fashion & Apparel Retail Studio",
            "flagshipAddr": "Shop No. 14, 1st Avenue, Anna Nagar East, Chennai, Tamil Nadu 600102",
            "branchAddr": "South Usman Road, T. Nagar, Chennai, Tamil Nadu 600017",
            "timings": "Monday – Sunday: 10:00 AM – 9:00 PM",
            "phone": "+91 98401 22345",
            "email": "contact@aminoid.example.com",
            "gstin": "33AAACD1234F1Z8",
            "categories": "Linen Shirts, Formal Blazers, Stretch Denim, Chinos, Festive Ethnic Wear, Belts & Accessories",
            "website": "https://aminoid.example.com"
        },
        "brand_profile": {
            "name": "aminoid Fashion & Apparel",
            "industry": "Fashion & Apparel Retail Studio",
            "tagline": "Own Your Style with aminoid.",
            "location": "Chennai, Tamil Nadu, India",
            "timings": "Monday – Sunday: 10:00 AM – 9:00 PM",
            "tone_of_voice": "Modern, confident, stylish, minimal, youthful, premium yet approachable.",
            "target_audience": "Men & women aged 18–40 in Chennai and metropolitan hubs interested in modern comfortable tailoring.",
            "brand_guidelines": "Clean studio lighting, minimal styling, authentic fabric textures, sharp silhouettes, bold typography.",
            "cta": "Explore aminoid in Chennai or DM to order. Own your style.",
            "avoid": "cheap look, heavy polyester, low contrast text, cluttered graphics",
            "key_themes": ["Linen Stories", "Tailored Elegance", "Casual Essentials", "Behind the Scenes", "Festive Drop"],
            "hashtags": ["#aminoid", "#FashionReels", "#ChennaiStyle", "#ModernTailoring", "#MenswearDaily"],
            "color_palette": ["#4f46e5", "#06b6d4", "#ec4899"],
            "meta_info": {
                "phone": "+91 98401 22345",
                "email": "contact@aminoid.example.com",
                "gstin": "33AAACD1234F1Z8",
                "flagshipAddr": "Shop No. 14, 1st Avenue, Anna Nagar East, Chennai, Tamil Nadu 600102",
                "branchAddr": "South Usman Road, T. Nagar, Chennai, Tamil Nadu 600017",
                "categories": "Linen Shirts, Formal Blazers, Stretch Denim, Chinos, Festive Ethnic Wear, Belts & Accessories",
                "icon": "👔"
            }
        }
    },
    {
        "name": "Apex Luxury Watches & Horology",
        "slug": "apex-luxury-watches",
        "description": "High-end bespoke mechanical timepieces, horological craftsmanship, and limited-edition chronographs.",
        "logo_url": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=400&q=80",
        "settings": {
            "industry": "Luxury Goods & Horology",
            "category": "Bespoke Timepieces & Fine Jewelry",
            "flagshipAddr": "Pavilion Mall, Level 2, MG Road, Bangalore, Karnataka 560001",
            "branchAddr": "Bandra Kurla Complex, G-Block, Mumbai, Maharashtra 400051",
            "timings": "Tuesday – Sunday: 11:00 AM – 8:30 PM",
            "phone": "+91 98200 44556",
            "email": "concierge@apexluxury.example.com",
            "gstin": "29AABCA9876E1Z2",
            "categories": "Automatic Chronographs, Tourbillons, Italian Leather Straps, Titanium Diver Watches",
            "website": "https://apexwatches.example.com"
        },
        "brand_profile": {
            "name": "Apex Luxury Watches",
            "industry": "Luxury Goods & Horology",
            "tagline": "Precision Engineered. Timeless Prestige.",
            "location": "Bangalore & Mumbai, India",
            "timings": "Tuesday – Sunday: 11:00 AM – 8:30 PM",
            "tone_of_voice": "Authoritative, sophisticated, elite, timeless, heritage-driven.",
            "target_audience": "Discerning watch collectors, C-level executives, luxury lifestyle connoisseurs aged 25–55.",
            "brand_guidelines": "Macro movement closeups, golden hour sapphire reflections, moody dark backgrounds, refined serif typography.",
            "cta": "Reserve your private boutique viewing or acquire your bespoke timepiece today.",
            "avoid": "cheap discounts, loud fluorescent colors, casual slang, generic stock photos",
            "key_themes": ["Horological Mastery", "Limited Editions", "Collector Stories", "Craftsmanship & Heritage"],
            "hashtags": ["#ApexLuxury", "#WatchCollector", "#HorologyArt", "#LuxuryTimepieces", "#Chronograph"],
            "color_palette": ["#1e293b", "#d97706", "#94a3b8"],
            "meta_info": {
                "phone": "+91 98200 44556",
                "email": "concierge@apexluxury.example.com",
                "gstin": "29AABCA9876E1Z2",
                "flagshipAddr": "Pavilion Mall, Level 2, MG Road, Bangalore, Karnataka 560001",
                "branchAddr": "Bandra Kurla Complex, G-Block, Mumbai, Maharashtra 400051",
                "categories": "Automatic Chronographs, Tourbillons, Italian Leather Straps, Titanium Diver Watches",
                "icon": "⌚"
            }
        }
    },
    {
        "name": "Zenith Organic Café & Roastery",
        "slug": "zenith-organic-cafe",
        "description": "Farm-to-cup artisanal specialty coffee, nitro cold brews, and plant-based organic bakery.",
        "logo_url": "https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?auto=format&fit=crop&w=400&q=80",
        "settings": {
            "industry": "Food & Beverage / Specialty Hospitality",
            "category": "Artisanal Roastery & Organic Bakery",
            "flagshipAddr": "82 Jubilee Hills Road, Hyderabad, Telangana 500033",
            "branchAddr": "Koramangala 4th Block, Bangalore, Karnataka 560034",
            "timings": "Monday – Sunday: 07:30 AM – 10:30 PM",
            "phone": "+91 97001 88990",
            "email": "hello@zenithcafe.example.com",
            "gstin": "36AAECZ4321B1Z5",
            "categories": "Single-Origin Pour Over, Nitro Cold Brew, Sourdough Bakes, Matcha Lattes, Roasted Whole Beans",
            "website": "https://zenithcafe.example.com"
        },
        "brand_profile": {
            "name": "Zenith Organic Café",
            "industry": "Specialty Coffee & Organic Roastery",
            "tagline": "Pure Origin. Conscious Living.",
            "location": "Hyderabad & Bangalore, India",
            "timings": "Monday – Sunday: 07:30 AM – 10:30 PM",
            "tone_of_voice": "Warm, welcoming, artisanal, eco-conscious, earthy, community-centric.",
            "target_audience": "Specialty coffee enthusiasts, remote creatives, sustainability-conscious foodies aged 20–45.",
            "brand_guidelines": "Natural morning sunlight, warm earth tones, artisan latte pours, sustainable kraft packaging.",
            "cta": "Experience fresh farm-to-cup roasts at our roastery or order your freshly roasted bag online.",
            "avoid": "industrial harsh lights, artificial flavoring mentions, cold corporate tone",
            "key_themes": ["Bean to Cup", "Morning Rituals", "Artisanal Baking", "Sustainability & Community"],
            "hashtags": ["#ZenithCafe", "#SpecialtyCoffee", "#FarmToCup", "#HyderabadFoodies", "#NitroBrew"],
            "color_palette": ["#059669", "#78350f", "#fef3c7"],
            "meta_info": {
                "phone": "+91 97001 88990",
                "email": "hello@zenithcafe.example.com",
                "gstin": "36AAECZ4321B1Z5",
                "flagshipAddr": "82 Jubilee Hills Road, Hyderabad, Telangana 500033",
                "branchAddr": "Koramangala 4th Block, Bangalore, Karnataka 560034",
                "categories": "Single-Origin Pour Over, Nitro Cold Brew, Sourdough Bakes, Matcha Lattes, Roasted Whole Beans",
                "icon": "☕"
            }
        }
    },
    {
        "name": "NovaTech AI Gadgets & Electronics",
        "slug": "novatech-ai-gadgets",
        "description": "Next-generation smart home devices, wearable AI hardware, and futuristic lifestyle gadgets.",
        "logo_url": "https://images.unsplash.com/photo-1550009158-9ebf69173e03?auto=format&fit=crop&w=400&q=80",
        "settings": {
            "industry": "Consumer Electronics & Smart Tech",
            "category": "Smart Hardware & Wearable Tech",
            "flagshipAddr": "Cyber City Hub, DLF Phase 2, Gurugram, Haryana 122002",
            "branchAddr": "Phoenix Marketcity, Whitefield, Bangalore, Karnataka 560048",
            "timings": "Monday – Sunday: 10:00 AM – 9:30 PM",
            "phone": "+91 99100 77889",
            "email": "support@novatech.example.com",
            "gstin": "06AABCN6543D1Z9",
            "categories": "Smart Ambient Hubs, AI Wireless Earbuds, IoT Home Automation, Holographic Displays",
            "website": "https://novatech.example.com"
        },
        "brand_profile": {
            "name": "NovaTech AI Gadgets",
            "industry": "Smart Hardware & Wearable Tech",
            "tagline": "Empowering Intelligent Living.",
            "location": "Gurugram & Bangalore, India",
            "timings": "Monday – Sunday: 10:00 AM – 9:30 PM",
            "tone_of_voice": "Futuristic, energetic, innovative, crisp, tech-savvy, inspiring.",
            "target_audience": "Tech early adopters, smart-home creators, developers, digital nomads aged 18–38.",
            "brand_guidelines": "Cyberpunk subtle neon glows, clean ergonomic 3D product renders, dynamic demo reels.",
            "cta": "Upgrade your daily ecosystem. Explore NovaTech's newest AI gear today.",
            "avoid": "vintage retro themes, slow animations, analog concepts, outdated jargon",
            "key_themes": ["Intelligent Living", "Next-Gen Audio", "Home Automation", "Unboxing & First Look"],
            "hashtags": ["#NovaTechAI", "#SmartHome", "#FutureTech", "#GadgetLovers", "#WearableAI"],
            "color_palette": ["#2563eb", "#06b6d4", "#6366f1"],
            "meta_info": {
                "phone": "+91 99100 77889",
                "email": "support@novatech.example.com",
                "gstin": "06AABCN6543D1Z9",
                "flagshipAddr": "Cyber City Hub, DLF Phase 2, Gurugram, Haryana 122002",
                "branchAddr": "Phoenix Marketcity, Whitefield, Bangalore, Karnataka 560048",
                "categories": "Smart Ambient Hubs, AI Wireless Earbuds, IoT Home Automation, Holographic Displays",
                "icon": "⚡"
            }
        }
    }
]


def init_database():
    print("Step 1: Creating database tables if missing...")
    Base.metadata.create_all(bind=engine)

    db: Session = SessionLocal()
    try:
        # Step 2: Seed or update Predefined Businesses
        print("Step 2: Initializing Predefined Businesses and Brand Profiles...")
        created_orgs = []
        for biz_data in PREDEFINED_BUSINESSES:
            org = db.query(Organization).filter(Organization.slug == biz_data["slug"]).first()
            if not org:
                # Also check by name
                org = db.query(Organization).filter(Organization.name == biz_data["name"]).first()

            if not org:
                org = Organization(
                    name=biz_data["name"],
                    slug=biz_data["slug"],
                    description=biz_data["description"],
                    logo_url=biz_data["logo_url"],
                    is_active=True,
                    settings=biz_data["settings"]
                )
                db.add(org)
                db.flush()
                print(f"  + Created business: {org.name} (ID: {org.id})")
            else:
                org.name = biz_data["name"]
                org.description = biz_data["description"]
                org.logo_url = biz_data["logo_url"]
                org.settings = biz_data["settings"]
                db.flush()
                print(f"  * Updated business: {org.name} (ID: {org.id})")

            created_orgs.append(org)

            # Ensure subscription exists
            sub = db.query(Subscription).filter(Subscription.organization_id == org.id).first()
            if not sub:
                sub = Subscription(
                    organization_id=org.id,
                    tier=SubscriptionTier.PRO,
                    status=SubscriptionStatus.ACTIVE
                )
                db.add(sub)

            # Ensure Brand Profile exists for this organization
            bp_data = biz_data["brand_profile"]
            bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
            if not bp:
                bp = BrandProfile(
                    organization_id=org.id,
                    name=bp_data["name"],
                    industry=bp_data["industry"],
                    tagline=bp_data["tagline"],
                    location=bp_data["location"],
                    timings=bp_data["timings"],
                    tone_of_voice=bp_data["tone_of_voice"],
                    target_audience=bp_data["target_audience"],
                    brand_guidelines=bp_data["brand_guidelines"],
                    cta=bp_data["cta"],
                    avoid=bp_data["avoid"],
                    key_themes=bp_data["key_themes"],
                    hashtags=bp_data["hashtags"],
                    color_palette=bp_data["color_palette"],
                    meta_info=bp_data["meta_info"],
                    logo_url=biz_data["logo_url"]
                )
                db.add(bp)
                print(f"    + Created Brand Profile: {bp.name}")
            else:
                bp.name = bp_data["name"]
                bp.industry = bp_data["industry"]
                bp.tagline = bp_data["tagline"]
                bp.location = bp_data["location"]
                bp.timings = bp_data["timings"]
                bp.tone_of_voice = bp_data["tone_of_voice"]
                bp.target_audience = bp_data["target_audience"]
                bp.brand_guidelines = bp_data["brand_guidelines"]
                bp.cta = bp_data["cta"]
                bp.avoid = bp_data["avoid"]
                bp.key_themes = bp_data["key_themes"]
                bp.hashtags = bp_data["hashtags"]
                bp.color_palette = bp_data["color_palette"]
                bp.meta_info = {**(bp.meta_info or {}), **bp_data["meta_info"]}
                if not bp.logo_url:
                    bp.logo_url = biz_data["logo_url"]
                print(f"    * Synchronized Brand Profile: {bp.name}")

        db.commit()

        # Step 3: Backfill user_organizations for existing users
        print("Step 3: Backfilling user_organizations assignments...")
        all_users = db.query(User).all()
        for user in all_users:
            if user.organization_id:
                existing_uo = db.query(UserOrganization).filter(
                    UserOrganization.user_id == user.id,
                    UserOrganization.organization_id == user.organization_id
                ).first()
                if not existing_uo:
                    db.add(UserOrganization(
                        user_id=user.id,
                        organization_id=user.organization_id,
                        role=user.role,
                        is_default=True
                    ))
                    print(f"  + Assigned User #{user.id} ({user.email}) to Organization #{user.organization_id}")

        # Step 4: Ensure Admin user (admin@socialai.com) exists with SUPER_ADMIN role and assigned to all predefined businesses
        print("Step 4: Ensuring Platform Admin user and assignments...")
        admin_user = db.query(User).filter(User.email == "admin@socialai.com").first()
        if not admin_user:
            admin_user = User(
                email="admin@socialai.com",
                hashed_password=get_password_hash("password123"),
                full_name="Platform Admin",
                role=UserRole.SUPER_ADMIN,
                organization_id=created_orgs[0].id,
                is_active=True,
                is_verified=True
            )
            db.add(admin_user)
            db.flush()
            print(f"  + Created Admin user: admin@socialai.com")
        else:
            admin_user.role = UserRole.SUPER_ADMIN
            if not admin_user.organization_id:
                admin_user.organization_id = created_orgs[0].id
            print(f"  * Verified Admin user: admin@socialai.com (SUPER_ADMIN)")

        # Assign Admin to all predefined businesses
        for org in created_orgs:
            admin_uo = db.query(UserOrganization).filter(
                UserOrganization.user_id == admin_user.id,
                UserOrganization.organization_id == org.id
            ).first()
            if not admin_uo:
                db.add(UserOrganization(
                    user_id=admin_user.id,
                    organization_id=org.id,
                    role=UserRole.SUPER_ADMIN,
                    is_default=(org.id == admin_user.organization_id)
                ))

        # Ensure demo user creator@hypergrowth.com is assigned to aminoid
        demo_user = db.query(User).filter(User.email == "creator@hypergrowth.com").first()
        if demo_user:
            aminoid_org = next((o for o in created_orgs if o.slug == "aminoid-fashion"), created_orgs[0])
            demo_user.organization_id = aminoid_org.id
            demo_uo = db.query(UserOrganization).filter(
                UserOrganization.user_id == demo_user.id,
                UserOrganization.organization_id == aminoid_org.id
            ).first()
            if not demo_uo:
                db.add(UserOrganization(
                    user_id=demo_user.id,
                    organization_id=aminoid_org.id,
                    role=UserRole.CREATOR,
                    is_default=True
                ))

        db.commit()
        print("[SUCCESS] Multi-business initialization complete!")
    except Exception as e:
        db.rollback()
        print(f"ERROR initializing multi-business: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_database()
