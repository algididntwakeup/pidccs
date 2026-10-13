import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Application router imports require OpenCV; keep the auth tests discoverable
# on lightweight environments that intentionally omit the image stack.
pytest.importorskip("cv2", reason="OpenCV is required to import the project routers")

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.routers import tracking
from app.models.sheet import Sheet


@pytest.mark.asyncio
async def test_auth_project_isolation_and_ground_truth_tracking(monkeypatch):
    import importlib.util
    if importlib.util.find_spec("aiosqlite") is None:
        pytest.skip("aiosqlite is required for async SQLite integration tests")
    from app.config import settings
    monkeypatch.setattr(settings, "BIMA_SSO_MOCK_MODE", True)
    monkeypatch.setattr(settings, "SUPERUSER_SECRET_KEY", "test-only-breakglass-secret")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(tracking, "AsyncSessionLocal", session_factory)
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/projects")).status_code == 401

            owner_response = await client.post("/api/v1/auth/sso-callback", json={
                "bima_user_id": "owner-bima-id",
                "email": "owner@example.test",
                "full_name": "Owner",
                "department": "Engineering",
            })
            other_response = await client.post("/api/v1/auth/sso-callback", json={
                "bima_user_id": "other-bima-id",
                "email": "other@example.test",
                "full_name": "Other",
                "department": "engineering",
            })
            bima_superuser_response = await client.post("/api/v1/auth/sso-callback", json={
                "bima_user_id": "executive-bima-id",
                "email": "executive@example.test",
                "full_name": "BIMA Executive",
                "department": "executive",
                "role": "superuser",
            })
            bima_flag_superuser_response = await client.post("/api/v1/auth/sso-callback", json={
                "bima_user_id": "flag-executive-bima-id",
                "email": "flag-executive@example.test",
                "full_name": "Flag Executive",
                "department": "finance",
                "is_bima_superuser": True,
            })
            denied_response = await client.post("/api/v1/auth/sso-callback", json={
                "bima_user_id": "blocked-bima-id",
                "email": "blocked@example.test",
                "full_name": "Non Engineering",
                "department": "operations",
                "role": "admin",
            })
            assert owner_response.status_code == 200
            assert other_response.status_code == 200
            assert owner_response.json()["user"]["department"] == "Engineering"
            assert owner_response.json()["user"]["role"] == "ENGINEER"
            assert owner_response.json()["user"]["is_superuser"] is False
            from app.core.security import decode_access_token
            owner_claims = decode_access_token(owner_response.json()["access_token"])
            assert owner_claims["department"] == "Engineering"
            assert owner_claims["role"] == "ENGINEER"
            assert owner_claims["is_superuser"] is False
            assert bima_superuser_response.status_code == 200
            assert bima_superuser_response.json()["user"]["department"] == "executive"
            assert bima_superuser_response.json()["user"]["role"] == "SUPERADMIN"
            assert bima_superuser_response.json()["user"]["is_superuser"] is True
            bima_superuser_claims = decode_access_token(bima_superuser_response.json()["access_token"])
            assert bima_superuser_claims["role"] == "SUPERADMIN"
            assert bima_superuser_claims["is_superuser"] is True
            assert bima_flag_superuser_response.status_code == 200
            assert bima_flag_superuser_response.json()["user"]["role"] == "SUPERADMIN"
            assert bima_flag_superuser_response.json()["user"]["is_superuser"] is True
            assert denied_response.status_code == 403
            assert denied_response.json()["detail"] == "Akses ditolak: Privilege tidak mencukupi"

            invalid_sudo = await client.post("/api/v1/auth/sudo-login", json={"secret_key": "wrong"})
            assert invalid_sudo.status_code == 401
            sudo_login = await client.post("/api/v1/auth/sudo-login", json={
                "secret_key": "test-only-breakglass-secret",
            })
            assert sudo_login.status_code == 200
            sudo_claims = decode_access_token(sudo_login.json()["access_token"])
            assert sudo_claims["sub"] == "sudo"
            assert sudo_claims["user_id"] == "sudo"
            assert sudo_claims["department"] == "IT"
            assert sudo_claims["role"] == "SUPERADMIN"
            assert sudo_claims["is_superuser"] is True
            assert "password" not in owner_response.json()["user"]
            assert "hashed_password" not in owner_response.json()["user"]

            # Repeated BIMA callbacks upsert the same account rather than making a duplicate.
            owner_again = await client.post("/api/v1/auth/sso-callback", json={
                "bima_user_id": "owner-bima-id",
                "email": "owner@example.test",
                "full_name": "Owner Updated",
                "department": "engineering",
            })
            assert owner_again.status_code == 200
            assert owner_again.json()["user"]["id"] == owner_response.json()["user"]["id"]
            assert owner_again.json()["user"]["department"] == "engineering"

            owner_token = owner_again.json()["access_token"]
            other_token = other_response.json()["access_token"]

            client.headers["Authorization"] = f"Bearer {owner_token}"
            created = await client.post("/api/v1/projects", json={"name": "Private project"})
            assert created.status_code == 201, created.text
            project = created.json()
            assert project["owner_id"] == owner_response.json()["user"]["id"]
            async with session_factory() as session:
                session.add(Sheet(
                    id="private-sheet",
                    project_id=project["id"],
                    filename="private.png",
                    file_path="private.png",
                ))
                await session.commit()

            client.headers["Authorization"] = f"Bearer {other_token}"
            assert (await client.get("/api/v1/projects")).json() == []
            assert (await client.get(f"/api/v1/projects/{project['id']}")).status_code == 404
            assert (await client.delete(f"/api/v1/projects/{project['id']}")).status_code == 404
            assert (await client.get(f"/api/v1/projects/{project['id']}/topology")).status_code == 404
            assert (await client.get("/api/v1/admin/monitoring")).status_code == 403
            assert (await client.get(
                f"/api/v1/projects/{project['id']}/sheets/private-sheet/result"
            )).status_code == 404

            # BIMA is a flat catalog: the engineer browses it and imports the rows
            # they work on. Importing makes them the local owner.
            catalog = await client.get("/api/v1/bima/projects")
            assert catalog.status_code == 200, catalog.text
            assert catalog.json(), "the mock BIMA catalog must not be empty"
            assert {"bima_project_id", "project_number", "name"} <= set(catalog.json()[0])

            client.headers["Authorization"] = f"Bearer {owner_token}"
            imported = await client.post("/api/v1/projects/import", json={
                "bima_project_id": "BIMA-CATALOG-001",
                "project_number": "BCD3-605-42",
                "name": "Unit 605 Amine Treating",
            })
            assert imported.status_code == 201, imported.text
            assigned_project_id = imported.json()["id"]
            assert imported.json()["owner_id"] == owner_response.json()["user"]["id"]
            assert imported.json()["project_number"] == "BCD3-605-42"

            # BIMA superusers have the same global project visibility as break-glass IT.
            client.headers["Authorization"] = f"Bearer {bima_superuser_response.json()['access_token']}"
            executive_projects = await client.get("/api/v1/projects")
            assert {item["id"] for item in executive_projects.json()} == {project["id"], assigned_project_id}
            assert (await client.get(f"/api/v1/projects/{project['id']}")).status_code == 200

            duplicate = await client.post("/api/v1/projects/import", json={
                "bima_project_id": "BIMA-CATALOG-001",
                "project_number": "BCD3-605-42",
                "name": "Unit 605 Amine Treating",
            })
            assert duplicate.status_code == 409

            client.headers["Authorization"] = f"Bearer {other_token}"
            assert (await client.get("/api/v1/projects")).json() == []
            assert (await client.get(f"/api/v1/projects/{assigned_project_id}")).status_code == 404
            assert (await client.get(f"/api/v1/projects/{project['id']}")).status_code == 404

            client.headers["Authorization"] = f"Bearer {owner_token}"
            tracked = await client.post("/api/v1/tracking/log", json={
                "project_id": project["id"],
                "action_type": "mark_pipe",
                "payload_json": {"run_idx": 0, "point": [10, 20]},
            })
            assert tracked.status_code == 202
            async with session_factory() as session:
                from app.models.ground_truth_log import GroundTruthLog

                logs = (await session.execute(select(GroundTruthLog))).scalars().all()
                assert len(logs) == 1
                assert logs[0].user_id == owner_response.json()["user"]["id"]
                assert logs[0].action_type == "mark_pipe"
                assert logs[0].payload_json == {"run_idx": 0, "point": [10, 20]}

            client.headers["Authorization"] = f"Bearer {sudo_login.json()['access_token']}"
            admin_projects = (await client.get("/api/v1/projects")).json()
            assert {item["id"] for item in admin_projects} == {project["id"], assigned_project_id}
            monitoring = await client.get("/api/v1/admin/monitoring")
            assert monitoring.status_code == 200, monitoring.text
            assert len(monitoring.json()["projects"]) == 2
            sheets_by_id = {item["id"]: item["sheets"] for item in monitoring.json()["projects"]}
            assert sheets_by_id[project["id"]]
            assert monitoring.json()["activities"][0]["user_email"] == "owner@example.test"
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
