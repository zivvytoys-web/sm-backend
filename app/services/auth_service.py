import uuid
import re
import time
from datetime import timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.user import User
from app.models.organization import Organization
from app.models.brand_profile import BrandProfile
from app.models.subscription import Subscription
from app.models.user_organization import UserOrganization
from app.schemas.auth import RegisterRequest, LoginRequest, Token
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_timed_token,
    decode_timed_token
)
from app.utils.enums import SubscriptionTier, SubscriptionStatus, UserRole



class AuthService:
    ADMIN_EMAIL = "superadmin@socialai.com"
    ADMIN_EMAILS = {"superadmin@socialai.com", "admin@socialai.com"}
    @staticmethod
    def register(db: Session, request: RegisterRequest) -> User:
        existing_user = db.query(User).filter(User.email == request.email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email address already exists."
            )

        assigned_org = None
        is_owner = False

        # Option A: User selected an existing predefined business
        if request.business_id:
            assigned_org = db.query(Organization).filter(
                Organization.id == request.business_id,
                Organization.is_active == True
            ).first()
            if not assigned_org:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Selected business ID {request.business_id} does not exist or is inactive."
                )

        # Option B: User specified a new custom organization / business name
        elif (request.organization_name and request.organization_name.strip()) or (request.company_name and request.company_name.strip()):
            org_name = (request.organization_name or request.company_name).strip()
            slug_base = re.sub(r'[^a-zA-Z0-9]', '-', org_name.lower()).strip('-')[:50] or 'workspace'
            org_slug = f"{slug_base}-{uuid.uuid4().hex[:8]}"
            assigned_org = Organization(name=org_name, slug=org_slug, is_active=True, settings={})
            db.add(assigned_org)
            db.flush()

            # Create default subscription
            sub = Subscription(
                organization_id=assigned_org.id,
                tier=SubscriptionTier.FREE,
                status=SubscriptionStatus.ACTIVE
            )
            db.add(sub)

            # Create default brand profile
            brand = BrandProfile(
                organization_id=assigned_org.id,
                name=org_name,
                industry="Retail & Creative Studio",
                tone_of_voice="Modern & Engaging",
                target_audience="Target Customers",
                brand_guidelines="Clean visuals and cohesive brand voice.",
                key_themes=["Brand Launch", "Product Highlights", "Storytelling"],
                hashtags=["#BrandLaunch", "#CreativeStudio"],
                color_palette=["#4f46e5", "#06b6d4", "#ec4899"],
                meta_info={}
            )
            db.add(brand)
            db.flush()
            is_owner = True

        # Option C: Neither given, create a dedicated workspace and brand profile for this user
        else:
            owner_name = (request.full_name or request.email.split('@')[0]).strip()
            org_name = f"{owner_name}'s Workspace"
            slug_base = re.sub(r'[^a-zA-Z0-9]', '-', owner_name.lower()).strip('-')[:40] or 'workspace'
            org_slug = f"{slug_base}-{uuid.uuid4().hex[:8]}"
            assigned_org = Organization(name=org_name, slug=org_slug, is_active=True, settings={})
            db.add(assigned_org)
            db.flush()

            # Create default subscription
            sub = Subscription(
                organization_id=assigned_org.id,
                tier=SubscriptionTier.FREE,
                status=SubscriptionStatus.ACTIVE
            )
            db.add(sub)

            # Create default brand profile for this user's organization
            brand = BrandProfile(
                organization_id=assigned_org.id,
                name=f"{owner_name}'s Brand",
                industry="Retail & Lifestyle",
                tone_of_voice="Modern & Engaging",
                target_audience="Target Customers",
                brand_guidelines="Clean visuals and cohesive brand voice.",
                key_themes=["Brand Launch", "Product Highlights", "Storytelling"],
                hashtags=[f"#{slug_base.replace('-', '')}", "#BrandLaunch"],
                color_palette=["#4f46e5", "#06b6d4", "#ec4899"],
                meta_info={}
            )
            db.add(brand)
            db.flush()
            is_owner = True

        # Determine role from registration request
        role_req = request.role
        if isinstance(role_req, str):
            role_val = role_req.lower().strip()
            if role_val in ["super_admin", "superadmin"]:
                user_role = UserRole.SUPER_ADMIN
            elif role_val == "admin":
                user_role = UserRole.ADMIN
            elif role_val == "manager":
                user_role = UserRole.MANAGER
            elif role_val == "viewer":
                user_role = UserRole.VIEWER
            else:
                user_role = UserRole.CREATOR
        elif isinstance(role_req, UserRole):
            user_role = role_req
        else:
            user_role = UserRole.CREATOR

        # Create user
        user = User(
            email=request.email,
            hashed_password=get_password_hash(request.password),
            full_name=request.full_name,
            role=user_role,
            organization_id=assigned_org.id if assigned_org else None,
            is_active=True,
            is_verified=True
        )
        db.add(user)
        db.flush()

        # If SuperAdmin, assign to all organizations
        if user_role == UserRole.SUPER_ADMIN:
            all_orgs = db.query(Organization).filter(Organization.is_active == True).all()
            for org in all_orgs:
                db.add(UserOrganization(
                    user_id=user.id,
                    organization_id=org.id,
                    role=UserRole.SUPER_ADMIN,
                    is_default=(org.id == assigned_org.id if assigned_org else False)
                ))
        elif assigned_org:
            # Create user_organizations assignment
            user_org = UserOrganization(
                user_id=user.id,
                organization_id=assigned_org.id,
                role=user_role,
                is_default=True
            )
            db.add(user_org)


        db.commit()
        db.refresh(user)
        return user

    _failed_login_attempts: Dict[str, List[float]] = {}

    @classmethod
    def check_rate_limit(cls, identifier: str, max_attempts: int = 5, window_seconds: int = 300) -> None:
        """Rate limit login attempts by client IP or email."""
        now = time.time()
        attempts = [t for t in cls._failed_login_attempts.get(identifier, []) if now - t < window_seconds]
        cls._failed_login_attempts[identifier] = attempts
        if len(attempts) >= max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Please try again after 5 minutes."
            )

    @classmethod
    def record_login_failure(cls, identifier: str) -> None:
        now = time.time()
        if identifier not in cls._failed_login_attempts:
            cls._failed_login_attempts[identifier] = []
        cls._failed_login_attempts[identifier].append(now)

    @classmethod
    def record_login_success(cls, identifier: str) -> None:
        cls._failed_login_attempts.pop(identifier, None)

    @classmethod
    def login(cls, db: Session, request: LoginRequest, client_identifier: Optional[str] = None) -> Token:
        rate_key = client_identifier or request.email.lower()
        cls.check_rate_limit(rate_key)

        user = db.query(User).filter(User.email == request.email).first()
        if not user:
            cls.record_login_failure(rate_key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password.",
                headers={"WWW-Authenticate": "Bearer"}
            )
        is_valid = verify_password(request.password, user.hashed_password)
        if not is_valid:
            cls.record_login_failure(rate_key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password.",
                headers={"WWW-Authenticate": "Bearer"}
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is deactivated."
            )

        # Successful login, reset rate limiter
        cls.record_login_success(rate_key)

        # Ensure user has a valid active organization_id and assignment
        if not user.organization_id:
            uo = db.query(UserOrganization).filter(UserOrganization.user_id == user.id).first()
            if uo:
                user.organization_id = uo.organization_id
                db.commit()
                db.refresh(user)

        access_token = create_access_token(subject=user.id)
        role_str = user.role.value if hasattr(user.role, 'value') else str(user.role)
        return Token(
            access_token=access_token,
            token_type="bearer",
            expires_in=60 * 24 * 60,
            role=role_str,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name
        )

    @classmethod
    def request_password_reset(cls, db: Session, email: str) -> Dict[str, Any]:
        """Generate a secure, signed password reset token with 1-hour expiration."""
        user = db.query(User).filter(User.email == email).first()
        if not user:
            # Prevent user enumeration
            return {
                "message": "If an account with that email exists, password reset instructions have been generated.",
                "reset_token": None
            }

        token = create_timed_token(subject=user.id, token_type="password_reset", expires_delta=timedelta(hours=1))
        return {
            "message": "Password reset instructions generated successfully.",
            "reset_token": token,
            "email": user.email
        }

    @classmethod
    def reset_password(cls, db: Session, token: str, new_password: str) -> Dict[str, Any]:
        """Validate password reset token and update password hash."""
        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 8 characters long."
            )

        payload = decode_timed_token(token, expected_type="password_reset")
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User associated with reset token not found."
            )

        user.hashed_password = get_password_hash(new_password)
        db.commit()
        return {"message": "Password successfully reset. You may now log in with your new password."}

    @classmethod
    def request_email_verification(cls, db: Session, email: str) -> Dict[str, Any]:
        """Generate an email verification token with 24-hour expiration."""
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {
                "message": "If an account with that email exists, verification instructions have been generated.",
                "verification_token": None
            }

        token = create_timed_token(subject=user.id, token_type="email_verification", expires_delta=timedelta(hours=24))
        return {
            "message": "Email verification token generated successfully.",
            "verification_token": token,
            "email": user.email
        }

    @classmethod
    def verify_email(cls, db: Session, token: str) -> Dict[str, Any]:
        """Validate email verification token and mark user as verified."""
        payload = decode_timed_token(token, expected_type="email_verification")
        user_id = payload.get("sub")
        user = db.query(User).filter(User.id == int(user_id)).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User associated with verification token not found."
            )

        user.is_verified = True
        db.commit()
        return {"message": "Email address successfully verified."}


    @staticmethod
    def get_available_businesses(db: Session) -> List[Dict[str, Any]]:
        """Public / Registration listing of active predefined businesses."""
        orgs = db.query(Organization).filter(Organization.is_active == True).order_by(Organization.id.asc()).all()
        result = []
        for org in orgs:
            bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
            settings = org.settings or {}
            result.append({
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "description": org.description,
                "logo_url": org.logo_url,
                "category": settings.get("category") or settings.get("industry") or (bp.industry if bp else "Business"),
                "location": settings.get("flagshipAddr") or (bp.location if bp else None),
                "brand_tagline": bp.tagline if bp else None,
                "color_palette": bp.color_palette if bp else ["#4f46e5", "#06b6d4"]
            })
        return result

    @staticmethod
    def get_user_businesses(db: Session, user: User) -> List[Dict[str, Any]]:
        """
        Returns businesses assigned to the user with strict multi-tenancy isolation.
        Enterprise tenant workspaces are strictly scoped to their own organization and brand profile.
        """
        # 1. Multi-Tenancy Enterprise Isolation:
        # An enterprise tenant account must ONLY see their own brand workspace and organization details.
        if user.organization_id:
            sub = db.query(Subscription).filter(
                Subscription.organization_id == user.organization_id,
                Subscription.status == SubscriptionStatus.ACTIVE
            ).first()
            if sub and sub.tier == SubscriptionTier.ENTERPRISE:
                org = db.query(Organization).filter(
                    Organization.id == user.organization_id,
                    Organization.is_active == True
                ).first()
                if org:
                    bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
                    return [{
                        "id": org.id,
                        "name": org.name,
                        "slug": org.slug,
                        "description": org.description,
                        "logo_url": org.logo_url,
                        "settings": org.settings or {},
                        "role": user.role.value if hasattr(user.role, 'value') else str(user.role),
                        "is_current": True,
                        "brand_profile": {
                            "id": bp.id if bp else None,
                            "name": bp.name if bp else org.name,
                            "industry": bp.industry if bp else None,
                            "tagline": bp.tagline if bp else None,
                            "color_palette": bp.color_palette if bp else ["#4f46e5", "#06b6d4"],
                            "meta_info": bp.meta_info if bp else {}
                        } if bp else None
                    }]

        # 2. Scoped assignments from UserOrganization:
        assignments = db.query(UserOrganization).filter(
            UserOrganization.user_id == user.id
        ).all()

        assigned_org_ids = [a.organization_id for a in assignments]
        if user.organization_id and user.organization_id not in assigned_org_ids:
            assigned_org_ids.append(user.organization_id)

        orgs = db.query(Organization).filter(
            Organization.id.in_(assigned_org_ids),
            Organization.is_active == True
        ).all()

        role_map = {a.organization_id: (a.role.value if hasattr(a.role, 'value') else str(a.role)) for a in assignments}

        result = []
        for org in orgs:
            bp = db.query(BrandProfile).filter(BrandProfile.organization_id == org.id).first()
            result.append({
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "description": org.description,
                "logo_url": org.logo_url,
                "settings": org.settings or {},
                "role": role_map.get(org.id, user.role.value if hasattr(user.role, 'value') else str(user.role)),
                "is_current": (org.id == user.organization_id),
                "brand_profile": {
                    "id": bp.id if bp else None,
                    "name": bp.name if bp else org.name,
                    "industry": bp.industry if bp else None,
                    "tagline": bp.tagline if bp else None,
                    "color_palette": bp.color_palette if bp else ["#4f46e5", "#06b6d4"],
                    "meta_info": bp.meta_info if bp else {}
                } if bp else None
            })
        return result

    @staticmethod
    def switch_user_business(db: Session, user: User, business_id: int) -> Dict[str, Any]:
        """Switch active business for current user, verifying assignment authorization and tenant suspension."""
        target_org = db.query(Organization).filter(Organization.id == business_id).first()
        if not target_org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Enterprise tenant #{business_id} not found."
            )

        is_super = user.role == UserRole.SUPER_ADMIN
        if not target_org.is_active and not is_super:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Enterprise tenant workspace is suspended. Contact Super Administrator."
            )

        if not is_super:
            # Check user_organizations assignment
            uo = db.query(UserOrganization).filter(
                UserOrganization.user_id == user.id,
                UserOrganization.organization_id == business_id
            ).first()
            if not uo and user.organization_id != business_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cross-tenant switching without authorization is prohibited."
                )

        # Update user active organization_id
        user.organization_id = business_id

        # Update is_default flag in assignments
        db.query(UserOrganization).filter(UserOrganization.user_id == user.id).update({"is_default": False})
        existing_uo = db.query(UserOrganization).filter(
            UserOrganization.user_id == user.id,
            UserOrganization.organization_id == business_id
        ).first()
        if existing_uo:
            existing_uo.is_default = True
        else:
            db.add(UserOrganization(
                user_id=user.id,
                organization_id=business_id,
                role=user.role,
                is_default=True
            ))

        db.commit()
        db.refresh(user)

        # Fetch active brand profile
        bp = db.query(BrandProfile).filter(BrandProfile.organization_id == business_id).first()
        access_token = create_access_token(subject=user.id)

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "active_business": {
                "id": target_org.id,
                "name": target_org.name,
                "slug": target_org.slug,
                "description": target_org.description,
                "logo_url": target_org.logo_url,
                "settings": target_org.settings or {},
                "brand_profile": {
                    "id": bp.id if bp else None,
                    "name": bp.name if bp else target_org.name,
                    "industry": bp.industry if bp else None,
                    "tagline": bp.tagline if bp else None,
                    "color_palette": bp.color_palette if bp else ["#4f46e5", "#06b6d4"],
                    "meta_info": bp.meta_info if bp else {}
                } if bp else None
            }
        }
