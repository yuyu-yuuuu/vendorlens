"""Optional shared bearer key for the local backend; not tenant authorization."""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from vendorlens.config import get_settings

bearer = HTTPBearer(auto_error=False)


def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    expected = get_settings().api_access_key
    if expected is None:
        return
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(),
        expected.get_secret_value().encode(),
    ):
        raise HTTPException(
            401, "invalid or missing API access key", headers={"WWW-Authenticate": "Bearer"}
        )
