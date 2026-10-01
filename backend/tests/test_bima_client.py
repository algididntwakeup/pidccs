import os
import sys
import asyncio

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.clients.bima import BimaClient
from app.db.base import Base
from app.models.project_assignment import ProjectAssignment
from app.models.user import User
from app.schemas.bima import BimaProjectRecord
from app.services.bima_sync_service import BimaProjectSyncService
from app.config import settings
from app.core.security import decode_access_token
from app.routers.auth import sudo_login
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
        if request.url.path == "/api/sso/verify":
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
        if request.url.path == "/api/projects":
            assert request.headers["Authorization"] == "Bearer upstream-sso-token"
            return httpx.Response(200, json={"data": [{
                "id": 901,
                "name": "Unit 901 P&ID",
                "description": "BIMA project record",
            }]})
        return httpx.Response(404)

    client = BimaClient(
        base_url="https://bima.example.test",
        transport=httpx.MockTransport(handle),
    )
    identity = await client.verify_sso_token("upstream-sso-token", "pidccs-client")
    admin_identity = await client.verify_sso_token("admin-token", "pidccs-client")
    projects = await client.fetch_assigned_projects(identity.bima_user_id, "upstream-sso-token")

    assert seen[0].url.path == "/api/sso/verify"
    assert seen[0].read() == b'{"token":"upstream-sso-token","client_id":"pidccs-client"}'
    assert identity.bima_user_id == "135"
    assert identity.email == "engineer@reksolindo.co.id"
    assert identity.role == "engineer"
    assert admin_identity.role == "SUPERADMIN"
    assert len(projects) == 1
    assert projects[0].bima_project_id == "901"
    assert projects[0].assigned_bima_user_ids == ["135"]


def test_bima_openapi_sso_verification_and_project_fetch():
    asyncio.run(_exercise_bima_openapi_client())


async def _exercise_project_assignment_sync():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with factory() as db:
            owner = User(bima_user_id="bima-1", email="one@example.test", role="engineer")
            assigned = User(bima_user_id="bima-2", email="two@example.test", role="engineer")
            db.add_all([owner, assigned])
            await db.flush()
            projects = await BimaProjectSyncService.sync_user_projects(db, owner, [BimaProjectRecord(
                bima_project_id="bima-project-1",
                name="Shared P&ID",
                assigned_bima_user_ids=["bima-1", "bima-2"],
            )])
            await db.commit()
            assert len(projects) == 1
            assert projects[0].owner_id is None
            assignments = (await db.scalars(select(ProjectAssignment))).all()
            assert {assignment.user_id for assignment in assignments} == {owner.id, assigned.id}

            await BimaProjectSyncService.sync_user_projects(db, owner, [BimaProjectRecord(
                bima_project_id="bima-project-1",
                name="Shared P&ID Updated",
                assigned_bima_user_ids=["bima-1", "bima-2"],
            )])
            await db.commit()
            assert len((await db.scalars(select(ProjectAssignment))).all()) == 2
    finally:
        await engine.dispose()


def test_bima_project_sync_creates_and_preserves_assignments():
    import importlib.util
    import pytest

    if importlib.util.find_spec("aiosqlite") is None:
        pytest.skip("aiosqlite is required for the async SQLite service test")
    asyncio.run(_exercise_project_assignment_sync())
