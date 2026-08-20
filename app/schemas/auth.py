from pydantic import BaseModel


class AuthUrlResponse(BaseModel):
    authorization_url: str | None
    mock_mode: bool
    message: str | None = None
