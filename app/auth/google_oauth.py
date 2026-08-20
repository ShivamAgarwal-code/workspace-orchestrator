"""Google OAuth2 authorization-code flow (real Google Cloud project required; unused entirely
when MOCK_GOOGLE_API=true).
"""
from app.config import get_settings


def _client_config() -> dict:
    settings = get_settings()
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    settings = get_settings()
    flow = Flow.from_client_config(_client_config(), scopes=settings.google_scopes_list, state=state)
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def build_authorization_url(state: str) -> str:
    flow = _flow(state)
    url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    return url


def exchange_code(code: str, state: str | None = None) -> dict:
    flow = _flow(state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {"access_token": creds.token, "refresh_token": creds.refresh_token, "expiry": creds.expiry}


def refresh_access_token(refresh_token: str) -> dict:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    settings = get_settings()
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return {"access_token": creds.token, "expiry": creds.expiry}
