import uuid

from app.models.user import User


async def authenticate_test_client(client, *, email=None, bima_user_id=None, department="engineering"):
    email = email or f"tester-{uuid.uuid4()}@example.test"
    bima_user_id = bima_user_id or email.split("@", 1)[0]
    response = await client.post("/api/v1/auth/sso-callback", json={
        "bima_user_id": bima_user_id,
        "email": email,
        "full_name": "API test user",
        "department": department,
    })
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return response.json()["user"]


def seeded_test_user(user_id="fixture-owner", email="fixture-owner@example.test"):
    return User(
        id=user_id,
        bima_user_id=user_id,
        email=email,
        full_name="Fixture owner",
        is_active=True,
        department="engineering",
        role="ENGINEER",
        is_superuser=False,
    )
