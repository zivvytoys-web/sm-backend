import uuid
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.core.permissions import get_current_active_user, require_creator
from app.core.config import settings
from app.core.logging import logger
from app.models.user import User
from app.models.brand_profile import BrandProfile
from app.models.social_account import SocialAccount
from app.services.facebook_oauth_service import (
    FacebookOAuthService,
    MetaGraphAPIError,
    handle_graph_api_error
)


router = APIRouter(prefix="/social", tags=["Facebook & Meta OAuth"])
meta_router = APIRouter(prefix="/meta", tags=["Meta Compliance & Webhooks"])


class FacebookConnectResponse(BaseModel):
    authorization_url: str
    state: str
    scopes: List[str]


class PageItemResponse(BaseModel):
    id: str
    name: str
    category: Optional[str] = None
    picture_url: Optional[str] = None
    instagram_business_account: Optional[Dict[str, Any]] = None


class SelectPageRequest(BaseModel):
    session_id: str = Field(description="Temporary session ID returned from OAuth callback")
    page_id: str = Field(description="Selected Facebook Page ID")
    brand_profile_id: Optional[int] = None
    connect_mode: Optional[str] = Field("both", description="Connection mode: 'both', 'facebook', or 'instagram'")


class TestConnectionRequest(BaseModel):
    account_id: int


class DataDeletionResponse(BaseModel):
    url: str
    confirmation_code: str


# =========================================================================
# 1. GET /api/social/facebook/connect
# =========================================================================

@router.get("/facebook/connect", response_model=FacebookConnectResponse)
def connect_facebook(
    brand_profile_id: Optional[int] = Query(None, description="Target brand profile ID"),
    reauthenticate: bool = Query(True, description="Force Facebook re-authentication / account switch prompt"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Step 1: Generate CSRF-safe state and build official Facebook OAuth 2.0 authorization URL.
    """
    try:
        res = FacebookOAuthService.generate_authorization_url(
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            brand_profile_id=brand_profile_id,
            db=db,
            reauthenticate=reauthenticate
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"[MetaOAuth] Connect endpoint failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initialize Facebook OAuth flow.")


# =========================================================================
# 2. GET /api/social/facebook/callback
# =========================================================================

@router.get("/facebook/callback")
async def facebook_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_reason: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Step 2: Meta OAuth callback handler.
    Validates state, exchanges code for long-lived Page tokens, stores temporary session,
    and redirects the browser to the frontend Page selection modal.
    """
    frontend_base = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    
    # Handle user cancellation / denial
    if error:
        err_msg = error_description or error_reason or error
        logger.info(f"[MetaOAuth] User denied or cancelled auth: {err_msg}")
        return RedirectResponse(
            url=f"{frontend_base}/dashboard?view=social-accounts&facebook_error={err_msg}"
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_base}/dashboard?view=social-accounts&facebook_error=Missing+authorization+code+or+state"
        )

    try:
        session_id, pages_list, state_data = await FacebookOAuthService.exchange_code_and_fetch_pages(
            code=code,
            state=state,
            db=db
        )

        return RedirectResponse(
            url=f"{frontend_base}/dashboard?view=social-accounts&facebook_auth=select_page&session_id={session_id}&pages_count={len(pages_list)}"
        )

    except MetaGraphAPIError as e:
        logger.error(f"[MetaOAuth] Graph API error on callback: {e.message}")
        return RedirectResponse(
            url=f"{frontend_base}/dashboard?view=social-accounts&facebook_error={e.message}"
        )
    except Exception as e:
        logger.exception(f"[MetaOAuth] Callback processing failed: {e}")
        return RedirectResponse(
            url=f"{frontend_base}/dashboard?view=social-accounts&facebook_error={str(e)}"
        )


# =========================================================================
# 3. GET /api/social/facebook/pages
# =========================================================================

@router.get("/facebook/pages", response_model=List[PageItemResponse])
def get_facebook_pages(
    session_id: str = Query(..., description="Session ID from OAuth callback"),
    current_user: User = Depends(require_creator)
):
    """
    Step 3: Retrieve temporary list of Facebook Pages (and linked Instagram accounts) WITHOUT raw tokens.
    """
    try:
        return FacebookOAuthService.get_temporary_pages(session_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# =========================================================================
# 4. POST /api/social/facebook/select-page
# =========================================================================

@router.post("/facebook/select-page")
async def select_facebook_page(
    payload: SelectPageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Step 4: Save selected Facebook Page with non-expiring encrypted Page token,
    verify with debug_token, and link Instagram Business Account if present.
    """
    try:
        res = await FacebookOAuthService.select_and_persist_page(
            db=db,
            current_user=current_user,
            session_id=payload.session_id,
            page_id=payload.page_id,
            brand_profile_id=payload.brand_profile_id,
            connect_mode=payload.connect_mode or "both"
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except MetaGraphAPIError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    except Exception as e:
        logger.exception(f"[MetaOAuth] Page selection failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to connect Facebook Page: {str(e)}")


# =========================================================================
# 5. GET /api/social/accounts
# =========================================================================

@router.get("/accounts")
def list_connected_social_accounts(
    brand_id: Optional[int] = Query(None, description="Filter by brand profile ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List connected social accounts with safe masked data (never exposes tokens).
    """
    query = db.query(SocialAccount).join(BrandProfile, SocialAccount.brand_profile_id == BrandProfile.id).filter(
        BrandProfile.organization_id == current_user.organization_id
    )

    if brand_id:
        query = query.filter(SocialAccount.brand_profile_id == brand_id)

    accounts = query.all()
    results = []
    for acc in accounts:
        meta = acc.meta_info or {}
        results.append({
            "id": acc.id,
            "platform": acc.platform,
            "account_name": acc.account_name,
            "account_id": acc.account_id,
            "profile_url": acc.profile_url,
            "is_connected": acc.is_connected,
            "brand_profile_id": acc.brand_profile_id,
            "verification_status": meta.get("verification_status", "ACTIVE"),
            "last_verified_at": meta.get("last_verified_at"),
            "scopes": meta.get("scopes", []),
            "created_at": acc.created_at
        })
    return results


# =========================================================================
# 6. POST /api/social/facebook/test
# =========================================================================

@router.post("/facebook/test")
async def test_facebook_connection(
    payload: TestConnectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Test live Graph API connection using the stored encrypted Page token.
    """
    try:
        return await FacebookOAuthService.test_page_connection(db, current_user, payload.account_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except MetaGraphAPIError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)


# =========================================================================
# 7. DELETE /api/social/facebook/{account_id}
# =========================================================================

@router.delete("/facebook/{account_id}")
async def disconnect_facebook(
    account_id: int,
    revoke: bool = Query(False, description="Optionally revoke Meta permissions"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_creator)
):
    """
    Disconnect Facebook Page and delete stored token.
    """
    try:
        await FacebookOAuthService.disconnect_facebook_account(db, current_user, account_id, revoke=revoke)
        return {"success": True, "message": "Facebook account disconnected successfully."}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# =========================================================================
# 8. META COMPLIANCE: DATA DELETION & DEAUTHORIZATION
# =========================================================================

@meta_router.post("/data-deletion", response_model=DataDeletionResponse)
async def meta_data_deletion(request: Request, db: Session = Depends(get_db)):
    """
    Meta Data Deletion Callback.
    Verifies signed_request using HMAC-SHA256 with Meta App Secret,
    deletes user's connected social data, and returns confirmation code.
    """
    form_data = await request.form()
    signed_request = form_data.get("signed_request")
    if not signed_request:
        raise HTTPException(status_code=400, detail="signed_request parameter missing")

    try:
        data = FacebookOAuthService.parse_signed_request(signed_request, db)
        user_id = data.get("user_id")
        code = f"DEL_{uuid.uuid4().hex[:12].upper()}"

        # Delete any accounts linked with this Meta user_id
        if user_id:
            accounts = db.query(SocialAccount).filter(SocialAccount.account_id == str(user_id)).all()
            for acc in accounts:
                db.delete(acc)
            db.commit()

        status_url = f"{settings.FRONTEND_URL}/data-deletion-status?code={code}"
        return DataDeletionResponse(url=status_url, confirmation_code=code)
    except Exception as e:
        logger.error(f"[MetaCompliance] Data deletion error: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid signed_request: {str(e)}")


@meta_router.get("/deauthorize")
@meta_router.post("/deauthorize")
async def meta_deauthorize(request: Request, db: Session = Depends(get_db)):
    """
    Meta Deauthorization Callback.
    Marks user's connected accounts disconnected when app access is removed from Facebook.
    """
    signed_request = None
    if request.method == "POST":
        try:
            form = await request.form()
            signed_request = form.get("signed_request")
        except Exception:
            pass

    if signed_request:
        try:
            data = FacebookOAuthService.parse_signed_request(signed_request, db)
            user_id = data.get("user_id")
            if user_id:
                accounts = db.query(SocialAccount).filter(SocialAccount.account_id == str(user_id)).all()
                for acc in accounts:
                    acc.is_connected = False
                db.commit()
        except Exception as e:
            logger.warning(f"[MetaCompliance] Deauth signed_request error: {e}")

    return {"status": "DEAUTHORIZED_SUCCESS"}
