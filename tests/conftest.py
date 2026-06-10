import pytest

from gym_tracker.auth.helpers import encode_token
from gym_tracker.services.services import create_gym, create_user, get_gym_by_invite_code


@pytest.fixture
def auth_headers_for_gym():
    created_users = 0

    def build_headers(gym_name, role="manager"):
        nonlocal created_users

        created_users += 1
        gym = get_gym_by_invite_code(gym_name)
        user = create_user(
            username=f"auth-fixture-user-{created_users}",
            password="test-password",
            gym_id=gym.id,
            role=role,
        )
        token = encode_token(
            {
                "username": user.username,
                "gym_id": user.gym_id,
                "role": user.role,
            }
        )

        return {"Authorization": f"Bearer {token}"}

    return build_headers


@pytest.fixture
def auth_headers(auth_headers_for_gym):
    gym_name = "Auth Fixture Gym"

    create_gym(gym_name, 10, invite_code=gym_name)

    return auth_headers_for_gym(gym_name)
