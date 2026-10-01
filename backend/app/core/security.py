from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt

from ..config import settings

ALGORITHM = "HS256"


def create_access_token(
    subject: str,
    expires_delta: Optional[timedelta] = None,
    claims: Optional[Dict[str, Any]] = None,
) -> str:
    expires = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    payload = dict(claims or {})
    payload.update({"sub": subject, "exp": expires})
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM])
