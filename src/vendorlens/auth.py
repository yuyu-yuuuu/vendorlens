"""Shared bearer key; optional only in development, not tenant authorization."""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from vendorlens.config import get_settings

bearer = HTTPBearer(auto_error=False)


def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    settings = get_settings()
    expected = settings.api_access_key
    if expected is None or not expected.get_secret_value().strip():
        if settings.environment.strip().lower() == "development":
            return
        raise HTTPException(503, "API access key is not configured")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(),
        expected.get_secret_value().encode(),
    ):
        raise HTTPException(
            401, "invalid or missing API access key", headers={"WWW-Authenticate": "Bearer"}
        )
