import httpx

from ..config import settings
from ..schemas.bima import BimaIdentity

class BimaServiceError(RuntimeError):
    pass

class BimaClient:
    """HTTP adapter for the BIMA OpenAPI SSO verification route.

    BIMA owns no per-project assignment data: its project API is a flat catalog
    that the engineer browses and imports through `/api/v1/bima/projects`. This
    client therefore only verifies identity.
    """

    def __init__(self, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        configured_base_url = settings.BIMA_BASE_URL if base_url is None else base_url
        self.base_url = configured_base_url.strip().rstrip("/")
        self.transport = transport

    def _require_base_url(self) -> str:
        if not self.base_url:
            raise BimaServiceError("BIMA_BASE_URL is not configured")
        return self.base_url

    async def verify_sso_token(self, token: str, client_id: str | None = None) -> BimaIdentity:
        base_url = self._require_base_url()
        body = {"token": token}
        effective_client_id = client_id or settings.BIMA_CLIENT_ID
        if effective_client_id:
            body["client_id"] = effective_client_id
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self.transport) as client:
                response = await client.post(f"{base_url}/api/sso/verify", json=body)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BimaServiceError("BIMA rejected or could not verify the SSO token") from exc

        envelope = response.json()
        if not isinstance(envelope, dict):
            raise BimaServiceError("BIMA returned an invalid SSO response")
        data = envelope.get("data", {})
        if not isinstance(data, dict):
            raise BimaServiceError("BIMA returned an invalid SSO profile")
        user = data.get("user", {})
        if not isinstance(user, dict):
            raise BimaServiceError("BIMA returned an invalid SSO user")
        if envelope.get("success") is False or data.get("valid") is False or not user:
            raise BimaServiceError("BIMA returned an invalid SSO profile")

        roles = user.get("roles") or []
        if not isinstance(roles, list):
            roles = []
        normalized_roles = [str(value).strip().casefold() for value in roles]
        declared_role = user.get("role")
        if user.get("is_admin") is True or str(declared_role or "").strip().casefold() in {
            "superadmin", "it_admin", "it-admin"
        } or any(value in {"superadmin", "it_admin", "it-admin"} for value in normalized_roles):
            role = "SUPERADMIN"
        else:
            role = declared_role or (roles[0] if roles else None)
        bima_user_id = str(user.get("bima_id") or user.get("bima_user_id") or "")
        email = str(user.get("email") or "")
        if not bima_user_id or "@" not in email:
            raise BimaServiceError("BIMA SSO profile is missing its stable ID or email")
        return BimaIdentity(
            bima_user_id=bima_user_id,
            email=email,
            full_name=str(user.get("name") or user.get("full_name") or ""),
            role=str(role or "engineer"),
        )
