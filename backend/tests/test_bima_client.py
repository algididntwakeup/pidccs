import os
import sys
import asyncio

import httpx
import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.clients.bima import BimaClient
from app.config import settings
from app.core.security import decode_access_token
from app.routers.auth import sudo_login
from app.routers.bima import list_bima_projects
from app.schemas.auth import SudoLoginRequest
from fastapi import HTTPException
from starlette.requests import Request

class _BreakGlassDB:
    def __init__(self):
        self.user = None

    async def get(self, model, user_id):
        assert user_id == "sudo"
        return self.user

    def add(self, user):
        self.user = user

    async def flush(self):
        return None

    async def rollback(self):
        return None

def test_sudo_login_requires_secret_and_issues_scoped_superadmin_jwt(monkeypatch):
    db = _BreakGlassDB()
    request = Request({
        "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/v1/auth/sudo-login", "raw_path": b"/api/v1/auth/sudo-login",
        "query_string": b"", "headers": [], "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    })

    async def attempt(secret):
        return await sudo_login(SudoLoginRequest(secret_key=secret), request, db)

    monkeypatch.setattr(settings, "SUPERUSER_SECRET_KEY", "unit-test-high-entropy-secret")
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(attempt("wrong-secret"))
    assert rejected.value.status_code == 401

    response = asyncio.run(attempt("unit-test-high-entropy-secret"))
    claims = decode_access_token(response.access_token)
    assert claims["sub"] == claims["user_id"] == "sudo"
    assert claims["role"] == "SUPERADMIN"
    assert claims["auth_method"] == "breakglass"
    assert db.user.id == "sudo"

async def _exercise_bima_openapi_client():
    seen = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        is_admin = b"admin-token" in request.content
        return httpx.Response(200, json={
            "success": True,
            "data": {
                "valid": True,
                "user": {
                    "bima_id": 135,
                    "email": "engineer@reksolindo.co.id",
                    "name": "Piping Engineer",
                    "roles": ["engineer", "reviewer"],
                    "is_admin": is_admin,
                },
            },
        })

    client = BimaClient(
        base_url="https://bima.example.test",
        transport=httpx.MockTransport(handle),
    )
    identity = await client.verify_sso_token("upstream-sso-token", "pidccs-client")
    admin_identity = await client.verify_sso_token("admin-token", "pidccs-client")

    assert seen[0].url.path == "/api/sso/verify"
    assert seen[0].read() == b'{"token":"upstream-sso-token","client_id":"pidccs-client"}'
    assert identity.bima_user_id == "135"
    assert identity.email == "engineer@reksolindo.co.id"
    assert identity.role == "engineer"
    assert admin_identity.role == "SUPERADMIN"

def test_bima_openapi_sso_verification():
    asyncio.run(_exercise_bima_openapi_client())

async def _exercise_flat_catalog():
    catalog = await list_bima_projects()
    assert catalog, "the mock BIMA catalog must not be empty"
    for record in catalog:
        assert record.bima_project_id
        assert record.project_number
        assert record.name
    # Flat directory: no per-user assignment field exists on a catalog row.
    assert not hasattr(catalog[0], "assigned_bima_user_ids")
    assert len({record.bima_project_id for record in catalog}) == len(catalog)

def test_bima_project_catalog_is_a_flat_directory():
    asyncio.run(_exercise_flat_catalog())
