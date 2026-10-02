import json
from typing import List, Protocol

import httpx

from ..config import settings
from ..schemas.bima import BimaIdentity, BimaProjectRecord


class BimaServiceError(RuntimeError):
    pass


class BimaClient:
    """HTTP adapter for the BIMA OpenAPI SSO verification and project routes."""

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

    async def fetch_assigned_projects(
        self,
        bima_user_id: str,
        access_token: str | None = None,
    ) -> List[BimaProjectRecord]:
        base_url = self._require_base_url()
        if not access_token:
            raise BimaServiceError("A current BIMA SSO token is required to sync projects")
        try:
            async with httpx.AsyncClient(timeout=15.0, transport=self.transport) as client:
                response = await client.get(
                    f"{base_url}/api/projects",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BimaServiceError("BIMA project fetch failed") from exc

        payload = response.json()
        records = payload
        if isinstance(payload, dict):
            records = payload.get("data", payload.get("projects", []))
            if isinstance(records, dict):
                records = records.get("projects", records.get("items", []))
        if not isinstance(records, list):
            raise BimaServiceError("BIMA project response was not a list")

        projects: List[BimaProjectRecord] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            project_id = item.get("bima_project_id", item.get("project_id", item.get("id")))
            name = item.get("name", item.get("project_name", item.get("title")))
            if project_id is None or not name:
                continue
            assigned = item.get("assigned_bima_user_ids", item.get("assigned_user_ids", []))
            if isinstance(assigned, (str, int)):
                assigned = [assigned]
            if not assigned:
                # The upstream route is called with this user's SSO token, so its
                # authorized result set is the assignment source when no membership
                # fields are documented by BIMA's OpenAPI response.
                assigned = [bima_user_id]
            projects.append(BimaProjectRecord(
                bima_project_id=str(project_id),
                name=str(name),
                description=str(item.get("description") or item.get("notes") or ""),
                assigned_bima_user_ids=[str(value) for value in assigned],
            ))
        return projects


class BimaProjectClient(Protocol):
    async def fetch_assigned_projects(
        self, bima_user_id: str, access_token: str | None = None
    ) -> List[BimaProjectRecord]: ...


class MockBimaProjectClient:
    """Local stand-in for BIMA's project-assignment API."""

    def __init__(self, catalog: list[dict] | None = None):
        source = catalog if catalog is not None else json.loads(settings.BIMA_MOCK_PROJECTS_JSON)
        self.catalog = [BimaProjectRecord.model_validate(item) for item in source]

    async def fetch_assigned_projects(
        self,
        bima_user_id: str,
        access_token: str | None = None,
    ) -> List[BimaProjectRecord]:
        return [
            project for project in self.catalog
            if bima_user_id in project.assigned_bima_user_ids
        ]
