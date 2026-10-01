import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_bima_sso_client, get_current_user
from ..clients.bima import BimaClient, BimaServiceError
from ..config import settings
from ..core.security import create_access_token
from ..db.session import get_db
from ..models.user import User
from ..schemas.auth import BimaSSOCallback, SSOCallbackResponse, SudoLoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/sso-callback", response_model=SSOCallbackResponse)
async def sso_callback(
    payload: BimaSSOCallback,
    bima_client: BimaClient = Depends(get_bima_sso_client),
    db: AsyncSession = Depends(get_db),
) -> SSOCallbackResponse:
    """Verify BIMA's SSO token or accept a configured development mock."""
    if payload.token:
        try:
            identity = await bima_client.verify_sso_token(payload.token, payload.client_id)
        except BimaServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="BIMA SSO token could not be verified",
            ) from exc
        bima_user_id = identity.bima_user_id.strip()
        email = identity.email.strip().lower()
        full_name = identity.full_name.strip()
        role = identity.role.strip()
    else:
        if not settings.BIMA_SSO_MOCK_MODE:
            raise HTTPException(status_code=401, detail="Mock BIMA callback is disabled")
        bima_user_id = (payload.bima_user_id or "").strip()
        email = (payload.email or "").strip().lower()
        full_name = payload.full_name.strip()
        role = payload.role.strip()
    if not bima_user_id or "@" not in email or not role:
        raise HTTPException(status_code=422, detail="BIMA identity, email, and role are required")

    user = await db.scalar(select(User).where(User.bima_user_id == bima_user_id))
    if user is None:
        user = User(
            bima_user_id=bima_user_id,
            email=email,
            full_name=full_name or email.split("@", 1)[0],
            role=role,
            is_active=True,
        )
        db.add(user)
    else:
        user.email = email
        user.full_name = full_name or user.full_name or email.split("@", 1)[0]
        user.role = role
        user.is_active = True

    try:
        await db.flush()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="BIMA identity conflicts with an existing account") from exc

    return SSOCallbackResponse(
        access_token=create_access_token(user.id),
        user=UserResponse.model_validate(user),
    )


@router.post("/sudo-login", response_model=TokenResponse)
async def sudo_login(
    payload: SudoLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Emergency IT break-glass login, disabled until a deployment secret is set."""
    configured_secret = settings.SUPERUSER_SECRET_KEY
    client_host = request.client.host if request.client else "unknown"
    if not configured_secret:
        logger.error("Break-glass login attempted while disabled from client=%s", client_host)
        raise HTTPException(status_code=503, detail="Break-glass login is not configured")
    if not hmac.compare_digest(payload.secret_key.encode("utf-8"), configured_secret.encode("utf-8")):
        logger.warning("Break-glass login rejected from client=%s", client_host)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid break-glass credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    sudo_user = await db.get(User, "sudo")
    if sudo_user is None:
        sudo_user = User(
            id="sudo",
            bima_user_id="local-breakglass-sudo",
            email="breakglass@localhost.invalid",
            full_name="Emergency IT Superuser",
            role="SUPERADMIN",
            is_active=True,
        )
        db.add(sudo_user)
    elif sudo_user.bima_user_id != "local-breakglass-sudo":
        logger.error("Reserved break-glass principal ID collision detected")
        raise HTTPException(status_code=503, detail="Break-glass principal is unavailable")
    else:
        sudo_user.role = "SUPERADMIN"
        sudo_user.is_active = True

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        logger.exception("Could not provision reserved break-glass principal")
        raise HTTPException(status_code=503, detail="Break-glass principal is unavailable") from exc
    logger.warning("Break-glass login succeeded user_id=sudo client=%s", client_host)
    return TokenResponse(access_token=create_access_token(
        "sudo",
        claims={"user_id": "sudo", "role": "SUPERADMIN", "auth_method": "breakglass"},
    ))


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
