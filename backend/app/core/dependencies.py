from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.bima import BimaClient
from ..config import settings
from ..db.session import get_db
from ..models.project import Project
from ..models.user import User
from .security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

def get_bima_sso_client() -> BimaClient:
    return BimaClient()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if settings.LOCAL_DEV_AUTH_BYPASS:
        # Reserved identity for the explicitly gated local workspace only.
        user = await db.get(User, "local-dev")
        if user is None:
            user = User(
                id="local-dev",
                bima_user_id="local-dev-bypass",
                email="local-dev@pidstudio.invalid",
                full_name="Local Development Engineer",
                is_active=True,
                department="engineering",
                role="SUPERADMIN",
                is_superuser=True,
            )
            db.add(user)
            await db.flush()
        elif user.bima_user_id != "local-dev-bypass":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Reserved local development identity is unavailable",
            )
        else:
            user.department = "engineering"
            user.role = "SUPERADMIN"
            user.is_superuser = True
        return user

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        token = credentials.credentials
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise unauthorized
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise unauthorized from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user

async def require_project_access(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    query = select(Project).where(Project.id == project_id)
    if not current_user.is_superuser:
        query = query.where(Project.owner_id == current_user.id)
    project = await db.scalar(query)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

# Kept as a compatibility alias for routers added before BIMA assignments.
require_project_owner = require_project_access

async def require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin role required")
    return current_user
