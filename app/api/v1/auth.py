"""Google OAuth2 endpoints. Entirely bypassed when MOCK_GOOGLE_API=true (the default) — the
authorization-url endpoint just tells the caller so, rather than 500ing on missing credentials."""
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.config import get_settings
from app.db.base import get_db
from app.db.models import User
from app.schemas.auth import AuthUrlResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/google", response_model=AuthUrlResponse)
async def start_google_oauth() -> AuthUrlResponse:
    settings = get_settings()
    if settings.mock_google_api:
        return AuthUrlResponse(
            authorization_url=None,
            mock_mode=True,
            message="MOCK_GOOGLE_API=true — Gmail/Calendar/Drive are served from seeded fixture "
            "data, no Google sign-in required. Set MOCK_GOOGLE_API=false with real "
            "GOOGLE_CLIENT_ID/SECRET to enable this flow.",
        )
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are not configured")

    from app.auth.google_oauth import build_authorization_url

    state = secrets.token_urlsafe(24)
    url = build_authorization_url(state)
    return AuthUrlResponse(authorization_url=url, mock_mode=False)


@router.get("/google/callback")
async def google_oauth_callback(
    code: str = Query(...),
    state: str | None = Query(default=None),
    user_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    if settings.mock_google_api:
        raise HTTPException(status_code=400, detail="OAuth callback hit while MOCK_GOOGLE_API=true")

    from app.auth.google_oauth import exchange_code

    tokens = exchange_code(code, state)

    from app.constants import DEMO_USER_ID

    target_id = user_id or DEMO_USER_ID
    user = await session.get(User, target_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"unknown user_id: {target_id}")

    user.google_access_token = tokens["access_token"]
    user.google_refresh_token = tokens["refresh_token"] or user.google_refresh_token
    user.google_token_expiry = tokens["expiry"]
    await session.commit()

    return RedirectResponse(url="/docs")
