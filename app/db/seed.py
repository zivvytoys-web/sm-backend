"""
Database seeder — creates predefined businesses, brand profiles, default logins,
and platform integrations if they do not already exist.

Called during application startup (lifespan) so the platform is immediately usable
on server deployments (Railway, Docker, etc.).
Never deletes or overwrites existing records.
"""
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.subscription import Subscription
from app.models.user_organization import UserOrganization
from app.core.security import get_password_hash
from app.utils.enums import UserRole, SubscriptionTier, SubscriptionStatus
from app.core.logging import logger


PREDEFINED_BUSINESSES = [
    {
        "name": "Social AI Headquarters",
        "slug": "socialai-hq",
        "description": "Enterprise Autonomous AI Social Media & Content Marketing Engine.",
        "logo_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=400&q=80",
        "settings": {
            "industry": "AI Marketing & Technology",
            "category": "Autonomous AI Agent Platform",
            "phone": "+91 98765 43210",
            "email": "admin@socialai.com",
            "categories": "AI Agents, Autonomous Social Scheduling, Creative Generation"
        },
        "brand_profile": {
            "name": "Social AI Headquarters",
            "industry": "AI Marketing & Technology",
            "tagline": "Autonomous Multi-Platform Social Intelligence.",
            "location": "Global",
            "timings": "24/7 Autonomous Operations",
            "tone_of_voice": "Intelligent, executive, forward-thinking, powerful.",
            "target_audience": "Brands, creators, agencies, and hyper-growth enterprises.",
            "brand_guidelines": "Sleek glassmorphism, modern gradients, concise high-impact copy.",
            "cta": "Experience autonomous social growth powered by Social AI.",
            "avoid": "clutter, outdated templates, manual delays",
            "key_themes": ["Autonomous Social", "Multi-Agent AI", "Omnichannel Growth"],
            "hashtags": ["#SocialAI", "#AutonomousMarketing", "#AIAgents", "#GrowthHacking"],
            "color_palette": ["#6366f1", "#a855f7", "#ec4899"],
            "meta_info": {
                "email": "admin@socialai.com",
                "icon": "🚀"
            }
        }
    }
]


def seed_database(db: Session) -> None:
    """
    Idempotent seeder — safe to call on every startup.
    Ensures all tables, predefined businesses, brand profiles, default logins,
    and platform integrations exist in the connected database (Railway MySQL, etc.).
    """
    try:
        from sqlalchemy import text
        try:
            db.execute(text("ALTER TABLE social_accounts MODIFY COLUMN platform VARCHAR(50) NOT NULL"))
            db.commit()
        except Exception:
            db.rollback()

        # 1. Seed or verify Predefined Businesses & Brand Profiles
        created_orgs = []
        for biz_data in PREDEFINED_BUSINESSES:
            org = db.query(Organization).filter(Organization.slug == biz_data["slug"]).first()
            if not org:
                org = db.query(Organization).filter(Organization.name == biz_data["name"]).first()

            if not org:
                org = Organization(
                    name=biz_data["name"],
                    slug=biz_data["slug"],
                    description=biz_data.get("description", ""),
                    logo_url=biz_data.get("logo_url", ""),
                    is_active=True,
                    settings=biz_data.get("settings", {})
                )
                db.add(org)
                db.flush()
                logger.info(f"Seeded business: {org.name} (id={org.id})")
            else:
                org.settings = biz_data.get("settings", org.settings)
                db.flush()

            created_orgs.append(org)

            # Ensure subscription exists
            sub = db.query(Subscription).filter(Subscription.organization_id == org.id).first()
            if not sub:
                sub = Subscription(
                    organization_id=org.id,
                    tier=SubscriptionTier.ENTERPRISE,
                    status=SubscriptionStatus.ACTIVE,
                    monthly_generation_limit=99999,
                    monthly_posts_limit=99999
                )
                db.add(sub)

            # Ensure Brand Profile exists for this organization
            bp_data = biz_data.get("brand_profile")
            if bp_data:
                bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
                if not bp:
                    bp = BrandProfile(
                        organization_id=org.id,
                        name=bp_data.get("name", org.name),
                        industry=bp_data.get("industry", "Business"),
                        tagline=bp_data.get("tagline", ""),
                        location=bp_data.get("location", ""),
                        timings=bp_data.get("timings", ""),
                        tone_of_voice=bp_data.get("tone_of_voice", "Professional"),
                        target_audience=bp_data.get("target_audience", "General"),
                        brand_guidelines=bp_data.get("brand_guidelines", ""),
                        cta=bp_data.get("cta", ""),
                        avoid=bp_data.get("avoid", ""),
                        key_themes=bp_data.get("key_themes", []),
                        hashtags=bp_data.get("hashtags", []),
                        color_palette=bp_data.get("color_palette", []),
                        meta_info=bp_data.get("meta_info", {}),
                        logo_url=biz_data.get("logo_url", "")
                    )
                    db.add(bp)
                else:
                    if not bp.logo_url and biz_data.get("logo_url"):
                        bp.logo_url = biz_data.get("logo_url")

        db.flush()

        # 2. Seed Default Logins: SuperAdmin & Admin Accounts
        primary_org = created_orgs[0] if created_orgs else _seed_default_organization(db)

        default_users = [
            {
                "email": "superadmin@socialai.com",
                "password": "password123",
                "full_name": "Super Admin",
                "role": UserRole.SUPER_ADMIN,
                "org_id": primary_org.id
            },
            {
                "email": "admin@socialai.com",
                "password": "password123",
                "full_name": "Admin",
                "role": UserRole.ADMIN,
                "org_id": primary_org.id
            }
        ]

        for u_data in default_users:
            user = db.query(User).filter(User.email == u_data["email"]).first()
            if not user:
                user = User(
                    email=u_data["email"],
                    hashed_password=get_password_hash(u_data["password"]),
                    full_name=u_data["full_name"],
                    role=u_data["role"],
                    organization_id=u_data["org_id"],
                    is_active=True,
                    is_verified=True
                )
                db.add(user)
                db.flush()
                logger.info(f"Seeded user: {user.email} ({user.role.value})")
            else:
                # Never overwrite existing passwords or credentials in production/startup
                if not user.organization_id:
                    user.organization_id = u_data["org_id"]
                db.flush()

            # Assign to organization
            uo = db.query(UserOrganization).filter(
                UserOrganization.user_id == user.id,
                UserOrganization.organization_id == user.organization_id
            ).first()
            if not uo:
                db.add(UserOrganization(
                    user_id=user.id,
                    organization_id=user.organization_id,
                    role=user.role,
                    is_default=True
                ))
            else:
                uo.role = user.role
                uo.is_default = True

        # 3. Seed Default Platform Integrations (YouTube, Facebook, Instagram, LinkedIn, Twitter/X)
        try:
            from app.services.platform_integration_service import PlatformIntegrationService
            PlatformIntegrationService.initialize_default_platforms(db)
            logger.info("Default platform integrations initialized.")
        except Exception as pi_err:
            logger.warning(f"Platform integrations initialization notice: {pi_err}")

        db.commit()
        logger.info("Database auto-initialization & seed check complete.")
    except Exception as e:
        logger.warning(f"Database seeder warning (non-fatal): {e}")
        db.rollback()


def _seed_default_organization(db: Session) -> Organization:
    """Ensure at least one organization exists fallback."""
    org = db.query(Organization).first()
    if not org:
        org = Organization(
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
        db.add(org)
        db.flush()
        db.commit()
        db.refresh(org)
    return org
