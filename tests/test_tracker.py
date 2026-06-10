import pytest
from datetime import datetime, timedelta

from gym_tracker.auth.helpers import encode_token
from gym_tracker.adapters.database import SessionLocal, configure_database
from gym_tracker.domain.models import (
    DeviceToken,
    Gym,
    GymFullError,
    Notification,
    OccupancyEvent,
    PendingNotification,
    User,
)
from gym_tracker.entrypoints.api import app
from gym_tracker.services.push_delivery import PushDeliveryService
from gym_tracker.services.push_providers import (
    FakePushProvider,
    FirebasePushProvider,
    InvalidFirebaseTokenError,
    PushProviderConfigurationError,
    PushProviderError,
    create_push_provider,
)
from gym_tracker.services.services import (
    create_notification,
    create_gym,
    create_user,
    enter_gym,
    generate_notifications_for_gym,
    get_capacity,
    get_gym_by_invite_code,
    get_gym_members,
    get_pending_notifications,
    get_user_notifications,
    get_users_to_notify,
    get_user_by_username,
    leave_gym,
    mark_pending_notification_failed,
    mark_pending_notification_sent,
    process_pending_notifications,
    register_device_token,
)


@pytest.fixture(autouse=True)
def use_empty_database(tmp_path):
    configure_database(f"sqlite:///{tmp_path / 'test_gym.db'}")


def test_created_gym_is_saved_to_database():
    create_gym("Reload Gym", 25)

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name="Reload Gym").first()

    assert gym is not None
    assert gym.max_capacity == 25


def test_get_gym_members_returns_usernames_and_roles():
    gym_name = "Members Service Gym"
    create_gym(gym_name, 25)
    gym = get_gym_by_invite_code(gym_name)
    create_user("members-manager", "test-password", gym.id, role="manager")
    create_user("members-lucio", "test-password", gym.id, role="member")

    members = get_gym_members(gym_name)

    assert members == [
        {"username": "members-lucio", "role": "member", "is_active": True},
        {"username": "members-manager", "role": "manager", "is_active": True},
    ]


def test_enter_gym_is_saved_to_database():
    create_gym("Saved Enter Gym", 10)

    enter_gym("Saved Enter Gym")

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name="Saved Enter Gym").first()

    assert gym.current_count == 1


def test_new_gym_starts_with_zero_people():
    gym = Gym(name="McFit Erding", max_capacity=80, current_count=0)

    assert gym.current_count == 0


def test_enter_increases_current_count_by_one():
    gym = Gym(name="McFit Erding", max_capacity=80, current_count=0)

    gym.enter()

    assert gym.current_count == 1


def test_gym_status_is_normal_below_70_percent():
    gym = Gym(name="Normal Gym", max_capacity=100, current_count=20)

    assert gym.status() == "NORMAL"


def test_gym_status_is_busy_from_70_percent():
    gym = Gym(name="Busy Gym", max_capacity=100, current_count=70)

    assert gym.status() == "BUSY"


def test_gym_status_is_full_from_100_percent():
    gym = Gym(name="Full Gym", max_capacity=100, current_count=100)

    assert gym.status() == "FULL"


def test_enter_raises_gym_full_error_when_gym_is_full():
    gym = Gym(name="Tiny Gym", max_capacity=1, current_count=0)

    gym.enter()

    with pytest.raises(GymFullError):
        gym.enter()


def test_leave_decreases_current_count_by_one():
    gym = Gym(name="McFit Erding", max_capacity=80, current_count=0)

    gym.enter()
    gym.leave()

    assert gym.current_count == 0

def test_enter_does_not_go_above_max_capacity():
    gym = Gym(name="McFit Erding", max_capacity=2, current_count=0)

    gym.enter()
    gym.enter()

    with pytest.raises(GymFullError):
        gym.enter()

def test_leave_does_not_go_below_zero():
    gym = Gym(name="McFit Erding", max_capacity=80, current_count=0)

    gym.leave()

    assert gym.current_count == 0

def test_enter_gym_through_service():
    create_gym("McFit Erding",2)

    enter_gym("McFit Erding")
    enter_gym("McFit Erding")

    with pytest.raises(GymFullError):
        enter_gym("McFit Erding")

    capacity = get_capacity("McFit Erding")

    assert capacity["current"] == 2
    assert capacity["max"] == 2


def test_get_gym_returns_correct_data(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Northside Fitness"

    create = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 50},
        headers=auth_headers,
    )
    assert create.status_code == 201

    gym_auth_headers = auth_headers_for_gym(name)
    enter = client.post(f"/gyms/{name}/enter", headers=gym_auth_headers)
    assert enter.status_code == 200

    response = client.get(f"/gyms/{name}")

    assert response.status_code == 200
    body = response.get_json()
    assert set(body.keys()) >= {"name", "current", "max", "is_full"}
    assert body["name"] == name
    assert body["current"] == 1
    assert body["max"] == 50
    assert body["is_full"] is False

def test_get_unknown_gym_returns_404():
    client = app.test_client()

    response = client.get("/gyms/Unknown")

    assert response.status_code == 404


def test_register_device_token_success(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={"token": "example-device-token", "platform": "ios"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "Device registered"
    assert "device_token" not in body["device"]
    assert body["device"]["device_display_name"] == "iOS Device"
    assert body["device"]["platform"] == "ios"
    assert body["device"]["push_enabled"] is True
    assert body["device"]["status"] == "active"

    with SessionLocal() as session:
        device_tokens = session.query(DeviceToken).all()

    assert len(device_tokens) == 1
    assert device_tokens[0].token == "example-device-token"
    assert device_tokens[0].platform == "ios"


def test_register_same_device_token_does_not_create_duplicate(auth_headers):
    client = app.test_client()

    first_response = client.post(
        "/devices/register",
        json={"token": "same-device-token", "platform": "ios"},
        headers=auth_headers,
    )
    second_response = client.post(
        "/devices/register",
        json={"token": "same-device-token", "platform": "android"},
        headers=auth_headers,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    with SessionLocal() as session:
        device_tokens = session.query(DeviceToken).all()

    assert len(device_tokens) == 1
    assert device_tokens[0].platform == "android"


def test_register_device_response_does_not_return_full_token(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={"device_token": "private-firebase-token", "platform": "ios"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert "private-firebase-token" not in str(body)
    assert "device_token" not in body["device"]


def test_register_device_token_accepts_device_token_only_payload(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={"device_token": "minimal-device-token"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["device"]["platform"] == "unknown"

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).one()

    assert device_token.token == "minimal-device-token"
    assert device_token.platform == "unknown"
    assert device_token.push_enabled is True


def test_register_device_token_stores_device_name_and_platform(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={
            "device_token": "named-device-token",
            "device_name": "Lucio iPhone",
            "platform": "ios",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).one()

    assert device_token.device_name == "Lucio iPhone"
    assert device_token.platform == "ios"


def test_register_device_token_keeps_device_name_optional(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={
            "device_token": "unnamed-device-token",
            "device_name": "   ",
            "platform": "ios",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).one()

    assert device_token.device_name is None


def test_register_device_token_strips_empty_device_name_to_none(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={
            "device_token": "empty-name-device-token",
            "device_name": "   ",
            "platform": "android",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["device"]["device_display_name"] == "Android Device"

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).one()

    assert device_token.device_name is None


def test_register_device_token_sets_and_updates_last_seen(auth_headers):
    client = app.test_client()

    first_response = client.post(
        "/devices/register",
        json={"device_token": "last-seen-device-token", "platform": "ios"},
        headers=auth_headers,
    )
    assert first_response.status_code == 200

    with SessionLocal() as session:
        first_last_seen = session.query(DeviceToken).one().last_seen

    second_response = client.post(
        "/devices/register",
        json={"device_token": "last-seen-device-token", "platform": "ios"},
        headers=auth_headers,
    )
    assert second_response.status_code == 200

    with SessionLocal() as session:
        second_last_seen = session.query(DeviceToken).one().last_seen

    assert first_last_seen is not None
    assert second_last_seen is not None
    assert second_last_seen >= first_last_seen


def test_update_my_device_updates_own_device_name(auth_headers):
    client = app.test_client()
    client.post(
        "/devices/register",
        json={"device_token": "update-name-token", "platform": "ios"},
        headers=auth_headers,
    )

    response = client.put(
        "/devices/me",
        json={"device_token": "update-name-token", "device_name": "Mein iPhone"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "Device updated"
    assert body["device"]["device_display_name"] == "Mein iPhone"
    assert "device_token" not in body["device"]

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).one()

    assert device_token.device_name == "Mein iPhone"


def test_update_my_device_updates_push_enabled(auth_headers):
    client = app.test_client()
    client.post(
        "/devices/register",
        json={"device_token": "update-push-token", "platform": "android"},
        headers=auth_headers,
    )

    response = client.put(
        "/devices/me",
        json={"device_token": "update-push-token", "push_enabled": False},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["device"]["push_enabled"] is False

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).one()

    assert device_token.push_enabled is False


def test_update_my_device_does_not_update_another_users_device(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    other_gym_name = "Device Update Other User Gym"
    create_gym(other_gym_name, 10, invite_code=other_gym_name)
    other_gym = get_gym_by_invite_code(other_gym_name)
    other_user = create_user("device-update-other-user", "test-password", other_gym.id)

    with SessionLocal() as session:
        session.add(
            DeviceToken(
                user_id=other_user.id,
                token="other-user-update-token",
                platform="ios",
                device_name="Original Alias",
            )
        )
        session.commit()

    own_headers = auth_headers_for_gym(other_gym_name, role="member")
    response = client.put(
        "/devices/me",
        json={"device_token": "other-user-update-token", "device_name": "Wrong Alias"},
        headers=own_headers,
    )

    assert response.status_code == 404

    with SessionLocal() as session:
        device_token = session.query(DeviceToken).filter_by(token="other-user-update-token").one()

    assert device_token.device_name == "Original Alias"


def test_delete_my_device_removes_own_device(auth_headers):
    client = app.test_client()
    client.post(
        "/devices/register",
        json={"device_token": "delete-own-token", "platform": "ios"},
        headers=auth_headers,
    )

    response = client.delete(
        "/devices/me",
        json={"device_token": "delete-own-token"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.get_json() == {"message": "Device removed"}

    with SessionLocal() as session:
        assert session.query(DeviceToken).count() == 0


def test_delete_my_device_does_not_delete_another_users_device(
    auth_headers_for_gym,
):
    client = app.test_client()
    gym_name = "Device Delete Other User Gym"
    create_gym(gym_name, 10, invite_code=gym_name)
    gym = get_gym_by_invite_code(gym_name)
    other_user = create_user("device-delete-other-user", "test-password", gym.id)

    with SessionLocal() as session:
        session.add(
            DeviceToken(
                user_id=other_user.id,
                token="other-user-delete-token",
                platform="android",
            )
        )
        session.commit()

    own_headers = auth_headers_for_gym(gym_name, role="member")
    response = client.delete(
        "/devices/me",
        json={"device_token": "other-user-delete-token"},
        headers=own_headers,
    )

    assert response.status_code == 404

    with SessionLocal() as session:
        assert session.query(DeviceToken).filter_by(token="other-user-delete-token").count() == 1


def test_register_device_token_requires_jwt():
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={"token": "example-device-token", "platform": "ios"},
    )

    assert response.status_code == 401


def test_manager_get_devices_returns_only_own_gym_devices(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    own_gym_name = "Device Manager Gym"
    other_gym_name = "Device Other Gym"
    create_gym(own_gym_name, 10, invite_code=own_gym_name)
    create_gym(other_gym_name, 10, invite_code=other_gym_name)
    own_gym = get_gym_by_invite_code(own_gym_name)
    other_gym = get_gym_by_invite_code(other_gym_name)
    own_user = create_user("device-own-user", "test-password", own_gym.id)
    other_user = create_user("device-other-user", "test-password", other_gym.id)
    own_user_headers = {
        "Authorization": "Bearer "
        + encode_token(
            {
                "username": own_user.username,
                "gym_id": own_user.gym_id,
                "role": own_user.role,
            }
        )
    }
    other_headers = {
        "Authorization": "Bearer "
        + encode_token(
            {
                "username": other_user.username,
                "gym_id": other_user.gym_id,
                "role": other_user.role,
            }
        )
    }

    client.post(
        "/devices/register",
        json={"token": "own-device-token", "platform": "ios"},
        headers=own_user_headers,
    )
    client.post(
        "/devices/register",
        json={"token": "other-device-token", "platform": "android"},
        headers=other_headers,
    )

    manager_headers = auth_headers_for_gym(own_gym_name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    devices = response.get_json()
    assert len(devices) == 1
    assert "device_token" not in devices[0]
    assert devices[0]["device_token_preview"] == "own-de...oken"
    assert devices[0]["device_name"] is None
    assert devices[0]["device_display_name"] == "iOS Device"
    assert devices[0]["platform"] == "ios"
    assert devices[0]["last_seen"] is not None
    assert devices[0]["push_enabled"] is True
    assert devices[0]["status"] == "active"
    assert "id" in devices[0]


def test_manager_get_devices_returns_mobile_app_fields(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Device Mobile Fields Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    user = create_user("device-mobile-fields-user", "test-password", gym.id)
    user_headers = {
        "Authorization": "Bearer "
        + encode_token(
            {
                "username": user.username,
                "gym_id": user.gym_id,
                "role": user.role,
            }
        )
    }
    client.post(
        "/devices/register",
        json={
            "device_token": "mobile-fields-token",
            "device_name": "Lucio iPhone",
            "platform": "ios",
        },
        headers=user_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    device = response.get_json()[0]
    assert "device_token" not in device
    assert device["device_token_preview"] == "mobile...oken"
    assert device["device_name"] == "Lucio iPhone"
    assert device["device_display_name"] == "Lucio iPhone"
    assert device["platform"] == "ios"
    assert datetime.fromisoformat(device["last_seen"]) is not None
    assert device["push_enabled"] is True
    assert device["status"] == "active"


def test_manager_get_devices_returns_token_preview_for_short_tokens(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Device Short Token Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    user = create_user("device-short-token-user", "test-password", gym.id)

    with SessionLocal() as session:
        session.add(DeviceToken(user_id=user.id, token="abc", platform="ios"))
        session.commit()

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    device = response.get_json()[0]
    assert "device_token" not in device
    assert device["device_token_preview"] == "••••abc"
    assert device["device_token_preview"]


def test_manager_get_devices_marks_recent_device_active(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Device Health Active Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    user = create_user("device-health-active-user", "test-password", gym.id)

    with SessionLocal() as session:
        session.add(
            DeviceToken(
                user_id=user.id,
                token="device-health-active-token",
                platform="ios",
                last_seen=datetime.now() - timedelta(days=1),
            )
        )
        session.commit()

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    assert response.get_json()[0]["device_health"] == "ACTIVE"


def test_manager_get_devices_marks_old_device_stale(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Device Health Stale Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    user = create_user("device-health-stale-user", "test-password", gym.id)

    with SessionLocal() as session:
        session.add(
            DeviceToken(
                user_id=user.id,
                token="device-health-stale-token",
                platform="android",
                last_seen=datetime.now() - timedelta(days=8),
            )
        )
        session.commit()

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    assert response.get_json()[0]["device_health"] == "STALE"


def test_manager_get_devices_marks_missing_last_seen_unknown(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Device Health Unknown Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    user = create_user("device-health-unknown-user", "test-password", gym.id)

    with SessionLocal() as session:
        device_token = DeviceToken(
            user_id=user.id,
            token="device-health-unknown-token",
            platform="unknown",
        )
        session.add(device_token)
        session.flush()
        device_token.last_seen = None
        session.commit()

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    device = response.get_json()[0]
    assert device["last_seen"] is None
    assert device["device_health"] == "UNKNOWN"


def test_manager_get_devices_uses_neutral_display_name_without_device_name(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Device Neutral Names Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    ios_user = create_user("device-neutral-ios-user", "test-password", gym.id)
    android_user = create_user("device-neutral-android-user", "test-password", gym.id)
    unknown_user = create_user("device-neutral-unknown-user", "test-password", gym.id)

    with SessionLocal() as session:
        session.add_all(
            [
                DeviceToken(user_id=ios_user.id, token="ios-token-1234", platform="ios"),
                DeviceToken(user_id=android_user.id, token="android-token-5678", platform="android"),
                DeviceToken(user_id=unknown_user.id, token="unknown-token-9999", platform="unknown"),
            ]
        )
        session.commit()

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/devices", headers=manager_headers)

    assert response.status_code == 200
    for device in response.get_json():
        assert "device_token" not in device

    display_names = {
        device["device_token_preview"]: device["device_display_name"]
        for device in response.get_json()
    }
    assert display_names["ios-to...1234"] == "iOS Device"
    assert display_names["androi...5678"] == "Android Device"
    assert display_names["unknow...9999"] == "Unknown Device"


def test_member_cannot_get_devices(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Device Member Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.get("/devices", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_trainer_cannot_get_devices(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Device Trainer Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.get("/devices", headers=trainer_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_register_device_token_rejects_invalid_platform(auth_headers):
    client = app.test_client()

    response = client.post(
        "/devices/register",
        json={"token": "invalid-platform-token", "platform": "web"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid platform"}

    with SessionLocal() as session:
        device_tokens = session.query(DeviceToken).all()

    assert device_tokens == []


def test_create_duplicate_gym_returns_400(auth_headers):
    client = app.test_client()
    name = "Duplicate Fitness"

    create = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 20},
        headers=auth_headers,
    )
    assert create.status_code == 201

    duplicate = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 20},
        headers=auth_headers,
    )

    assert duplicate.status_code == 400
    assert duplicate.get_json() == {"error": "Gym already exists"}


def test_leave_gym_through_api(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Northside Fitness Leave"

    create = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 50},
        headers=auth_headers,
    )
    assert create.status_code == 201

    gym_auth_headers = auth_headers_for_gym(name)
    enter = client.post(f"/gyms/{name}/enter", headers=gym_auth_headers)
    assert enter.status_code == 200

    leave = client.post(f"/gyms/{name}/leave", headers=gym_auth_headers)
    assert leave.status_code == 200

    body = leave.get_json()
    assert body["name"] == name
    assert body["current"] == 0
    assert body["max"] == 50
    assert body["is_full"] is False


def test_enter_and_leave_my_gym_through_api(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Manager Scoped Gym"

    create = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 50},
        headers=auth_headers,
    )
    assert create.status_code == 201

    gym_auth_headers = auth_headers_for_gym(name)
    enter = client.post("/my-gym/enter", headers=gym_auth_headers)
    assert enter.status_code == 200

    enter_body = enter.get_json()
    assert enter_body["name"] == name
    assert enter_body["capacity"]["current"] == 1

    leave = client.post("/my-gym/leave", headers=gym_auth_headers)
    assert leave.status_code == 200

    leave_body = leave.get_json()
    assert leave_body["name"] == name
    assert leave_body["current"] == 0


def test_leave_unknown_gym_returns_404(auth_headers):
    client = app.test_client()

    response = client.post("/gyms/UnknownGym/leave", headers=auth_headers)

    assert response.status_code == 404


def test_enter_unknown_gym_returns_404(auth_headers):
    client = app.test_client()

    response = client.post("/gyms/UnknownGym/enter", headers=auth_headers)

    assert response.status_code == 404


def test_enter_other_tenant_gym_returns_403(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Tenant Manager Gym"
    other_gym_name = "Tenant Other Gym"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )

    own_gym_headers = auth_headers_for_gym(own_gym_name)
    response = client.post(
        f"/gyms/{other_gym_name}/enter",
        headers=own_gym_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}


def test_get_gym_members_endpoint_returns_members(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Members API Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user("members-api-lucio", "test-password", gym.id, role="member")

    gym_auth_headers = auth_headers_for_gym(name)
    response = client.get(f"/gyms/{name}/members", headers=gym_auth_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 2
    assert {"username": "members-api-lucio", "role": "member", "is_active": True} in body
    assert any(
        member["username"].startswith("auth-fixture-user-")
        and member["role"] == "manager"
        and member["is_active"] is True
        for member in body
    )


def test_get_other_tenant_gym_members_returns_403(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Members Own Gym"
    other_gym_name = "Members Other Gym"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )

    own_gym_headers = auth_headers_for_gym(own_gym_name)
    response = client.get(
        f"/gyms/{other_gym_name}/members",
        headers=own_gym_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}


def test_trainer_can_get_gym_members(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Members Trainer Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.get(f"/gyms/{name}/members", headers=trainer_headers)

    assert response.status_code == 200


def test_member_cannot_get_gym_members(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Members Role Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.get(f"/gyms/{name}/members", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Trainer or manager access required"}


def test_trainer_can_deactivate_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Deactivate Trainer Gym"
    username = "trainer-deactivate-member"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.post(f"/users/{username}/deactivate", headers=trainer_headers)

    assert response.status_code == 200
    assert response.get_json() == {
        "message": "User deactivated successfully",
        "username": username,
        "is_active": False,
    }
    assert get_user_by_username(username).is_active is False


def test_trainer_can_activate_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activate Trainer Gym"
    username = "trainer-activate-member"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")
    with SessionLocal() as session:
        session.query(User).filter_by(username=username).update({"is_active": False})
        session.commit()

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.post(f"/users/{username}/activate", headers=trainer_headers)

    assert response.status_code == 200
    assert response.get_json() == {
        "message": "User activated successfully",
        "username": username,
        "is_active": True,
    }
    assert get_user_by_username(username).is_active is True


def test_manager_can_deactivate_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Deactivate Manager Gym"
    username = "manager-deactivate-member"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.post(f"/users/{username}/deactivate", headers=manager_headers)

    assert response.status_code == 200
    assert get_user_by_username(username).is_active is False


def test_manager_can_activate_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activate Manager Gym"
    username = "manager-activate-member"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")
    with SessionLocal() as session:
        session.query(User).filter_by(username=username).update({"is_active": False})
        session.commit()

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.post(f"/users/{username}/activate", headers=manager_headers)

    assert response.status_code == 200
    assert get_user_by_username(username).is_active is True


def test_member_cannot_deactivate_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Deactivate Member Block Gym"
    username = "member-deactivate-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.post(f"/users/{username}/deactivate", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Trainer or manager access required"}
    assert get_user_by_username(username).is_active is True


def test_deactivate_member_from_other_gym_is_blocked(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Deactivate Own Gym"
    other_gym_name = "Deactivate Other Gym"
    username = "other-gym-deactivate-target"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    other_gym = get_gym_by_invite_code(other_gym_name)
    create_user(username, "test-password", other_gym.id, role="member")

    trainer_headers = auth_headers_for_gym(own_gym_name, role="trainer")
    response = client.post(f"/users/{username}/deactivate", headers=trainer_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}
    assert get_user_by_username(username).is_active is True


def test_deactivated_user_cannot_login(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Inactive Login Gym"
    username = "inactive-login-user"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    client.post(f"/users/{username}/deactivate", headers=manager_headers)

    response = client.post(
        "/login",
        json={"username": username, "password": "test-password"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "User is inactive"}


def test_role_change_creates_activity_log(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activity Role Gym"
    username = "activity-role-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    client.put(
        f"/users/{username}/role",
        json={"role": "trainer"},
        headers=manager_headers,
    )

    response = client.get(f"/gyms/{name}/activity-log", headers=manager_headers)

    assert response.status_code == 200
    logs = response.get_json()
    assert logs[0]["action"] == "role_changed"
    assert logs[0]["target_username"] == username
    assert logs[0]["details"] == "Role changed from member to trainer"
    assert logs[0]["actor_username"].startswith("auth-fixture-user-")
    assert "created_at" in logs[0]


def test_deactivate_creates_activity_log(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activity Deactivate Gym"
    username = "activity-deactivate-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    manager_headers = auth_headers_for_gym(name, role="manager")
    client.post(f"/users/{username}/deactivate", headers=trainer_headers)

    response = client.get(f"/gyms/{name}/activity-log", headers=manager_headers)

    assert response.status_code == 200
    logs = response.get_json()
    assert logs[0]["action"] == "user_deactivated"
    assert logs[0]["target_username"] == username
    assert logs[0]["details"] == "User deactivated"


def test_activate_creates_activity_log(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activity Activate Gym"
    username = "activity-activate-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    manager_headers = auth_headers_for_gym(name, role="manager")
    client.post(f"/users/{username}/deactivate", headers=trainer_headers)
    client.post(f"/users/{username}/activate", headers=trainer_headers)

    response = client.get(f"/gyms/{name}/activity-log", headers=manager_headers)

    assert response.status_code == 200
    logs = response.get_json()
    assert logs[0]["action"] == "user_activated"
    assert logs[0]["target_username"] == username
    assert logs[0]["details"] == "User reactivated"


def test_manager_can_get_activity_log(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activity Manager View Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get(f"/gyms/{name}/activity-log", headers=manager_headers)

    assert response.status_code == 200
    assert response.get_json() == []


def test_trainer_and_member_cannot_get_activity_log(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Activity Role Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    member_headers = auth_headers_for_gym(name, role="member")

    trainer_response = client.get(f"/gyms/{name}/activity-log", headers=trainer_headers)
    member_response = client.get(f"/gyms/{name}/activity-log", headers=member_headers)

    assert trainer_response.status_code == 403
    assert trainer_response.get_json() == {"error": "Manager access required"}
    assert member_response.status_code == 403
    assert member_response.get_json() == {"error": "Manager access required"}


def test_activity_log_from_other_gym_is_blocked(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Activity Own Gym"
    other_gym_name = "Activity Other Gym"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )

    own_manager_headers = auth_headers_for_gym(own_gym_name, role="manager")
    response = client.get(
        f"/gyms/{other_gym_name}/activity-log",
        headers=own_manager_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}


def test_manager_can_remove_member_from_own_gym(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Remove Member Gym"
    username = "remove-member-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.delete(f"/users/{username}", headers=manager_headers)

    assert response.status_code == 200
    assert response.get_json() == {
        "username": username,
        "message": "Member removed",
    }
    assert get_user_by_username(username) is None


def test_remove_unknown_member_returns_404(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Remove Unknown Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.delete("/users/missing-member", headers=manager_headers)

    assert response.status_code == 404
    assert response.get_json() == {"error": "User not found"}


def test_manager_cannot_remove_member_from_other_gym(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Remove Own Gym"
    other_gym_name = "Remove Other Gym"
    username = "other-gym-member"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    other_gym = get_gym_by_invite_code(other_gym_name)
    create_user(username, "test-password", other_gym.id, role="member")

    own_manager_headers = auth_headers_for_gym(own_gym_name, role="manager")
    response = client.delete(f"/users/{username}", headers=own_manager_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}
    assert get_user_by_username(username) is not None


def test_member_cannot_remove_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Remove Blocked Member Gym"
    username = "member-remove-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.delete(f"/users/{username}", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}
    assert get_user_by_username(username) is not None


def test_manager_cannot_remove_self(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Remove Self Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    manager_username = get_gym_members(name)[0]["username"]
    response = client.delete(f"/users/{manager_username}", headers=manager_headers)

    assert response.status_code == 400
    assert response.get_json() == {"error": "You cannot remove yourself"}
    assert get_user_by_username(manager_username) is not None


def test_manager_can_update_member_role_to_trainer(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Update Gym"
    username = "role-update-member"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "trainer"},
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "message": "Role updated successfully",
        "username": username,
        "role": "trainer",
    }
    assert get_user_by_username(username).role == "trainer"


def test_manager_can_update_trainer_role_to_member(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Downgrade Gym"
    username = "role-update-trainer"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="trainer")

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "member"},
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "message": "Role updated successfully",
        "username": username,
        "role": "member",
    }
    assert get_user_by_username(username).role == "member"


def test_member_cannot_update_member_role(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Member Block Gym"
    username = "role-member-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "trainer"},
        headers=member_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}
    assert get_user_by_username(username).role == "member"


def test_trainer_cannot_update_member_role(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Trainer Block Gym"
    username = "role-trainer-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "manager"},
        headers=trainer_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}
    assert get_user_by_username(username).role == "member"


def test_manager_cannot_update_role_for_user_from_other_gym(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Role Own Gym"
    other_gym_name = "Role Other Gym"
    username = "role-other-gym-member"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    other_gym = get_gym_by_invite_code(other_gym_name)
    create_user(username, "test-password", other_gym.id, role="member")

    own_manager_headers = auth_headers_for_gym(own_gym_name, role="manager")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "trainer"},
        headers=own_manager_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}
    assert get_user_by_username(username).role == "member"


def test_update_member_role_rejects_invalid_role(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Invalid Gym"
    username = "role-invalid-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "superuser"},
        headers=manager_headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid role"}
    assert get_user_by_username(username).role == "member"


def test_update_member_role_rejects_manager_role(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Manager Target Gym"
    username = "role-manager-target"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(username, "test-password", gym.id, role="member")

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.put(
        f"/users/{username}/role",
        json={"role": "manager"},
        headers=manager_headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid role"}
    assert get_user_by_username(username).role == "member"


def test_update_member_role_rejects_manager_user_target(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Manager User Target Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    manager_username = get_gym_members(name)[0]["username"]
    response = client.put(
        f"/users/{manager_username}/role",
        json={"role": "member"},
        headers=manager_headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Only member and trainer roles can be changed"}
    assert get_user_by_username(manager_username).role == "manager"


def test_update_member_role_handles_unknown_user(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Role Unknown Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.put(
        "/users/missing-role-user/role",
        json={"role": "trainer"},
        headers=manager_headers,
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "User not found"}


def test_manager_can_generate_invite_code_from_management_role(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Invite Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10, "invite_code": "OLD-CODE"},
        headers=auth_headers,
    )

    gym_auth_headers = auth_headers_for_gym("OLD-CODE")
    response = client.post(f"/gyms/{name}/invite-code", headers=gym_auth_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == name
    assert body["invite_code"].startswith("INVITEMA-")
    assert body["invite_code"] != "OLD-CODE"
    assert " " not in body["invite_code"]


def test_manager_can_generate_invite_code(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Invite Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10, "invite_code": "MANAGER-OLD"},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym("MANAGER-OLD", role="manager")
    response = client.post(f"/gyms/{name}/invite-code", headers=manager_headers)

    assert response.status_code == 200


def test_member_cannot_generate_invite_code(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Invite Member Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10, "invite_code": "MEMBER-ROLE"},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym("MEMBER-ROLE", role="member")
    response = client.post(f"/gyms/{name}/invite-code", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_trainer_cannot_generate_invite_code(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Invite Trainer Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10, "invite_code": "TRAINER-ROLE"},
        headers=auth_headers,
    )

    trainer_headers = auth_headers_for_gym("TRAINER-ROLE", role="trainer")
    response = client.post(f"/gyms/{name}/invite-code", headers=trainer_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_manager_can_update_gym_settings(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Settings Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.put(
        f"/gyms/{name}/settings",
        json={"max_capacity": 15},
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["max_capacity"] == 15


def test_trainer_cannot_update_gym_settings(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Settings Trainer Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.put(
        f"/gyms/{name}/settings",
        json={"max_capacity": 15},
        headers=trainer_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_manager_cannot_update_other_tenant_gym_settings(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    own_gym_name = "Settings Manager Own Gym"
    other_gym_name = "Settings Manager Other Gym"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(own_gym_name, role="manager")
    response = client.put(
        f"/gyms/{other_gym_name}/settings",
        json={"max_capacity": 15},
        headers=manager_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied for this gym"}


def test_generated_invite_code_is_saved(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Stored Invite Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10, "invite_code": "STORED-OLD"},
        headers=auth_headers,
    )

    gym_auth_headers = auth_headers_for_gym("STORED-OLD")
    response = client.post(f"/gyms/{name}/invite-code", headers=gym_auth_headers)
    new_invite_code = response.get_json()["invite_code"]
    stored_gym = get_gym_by_invite_code(new_invite_code)

    assert stored_gym is not None
    assert stored_gym.name == name


def test_my_gym_returns_invite_code(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "My Gym Invite"
    invite_code = "MYGYM-INVITE"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10, "invite_code": invite_code},
        headers=auth_headers,
    )

    gym_auth_headers = auth_headers_for_gym(invite_code)
    response = client.get("/my-gym", headers=gym_auth_headers)

    assert response.status_code == 200
    assert response.get_json()["gym"]["invite_code"] == invite_code


def test_user_registration_defaults_notification_threshold_to_off():
    client = app.test_client()
    gym_name = "Notification Default Gym"

    create_gym(gym_name, 10, invite_code="NOTIFY-DEFAULT")

    response = client.post(
        "/users",
        json={
            "username": "notify-default-user",
            "password": "test-password",
            "invite_code": "NOTIFY-DEFAULT",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["notification_threshold"] == "off"
    assert response.get_json()["notification_threshold_count"] == "off"
    assert get_user_by_username("notify-default-user").notification_threshold_count == "off"


def test_user_registration_stores_valid_notification_threshold():
    client = app.test_client()
    gym_name = "Notification Valid Gym"

    create_gym(gym_name, 10, invite_code="NOTIFY-VALID")

    response = client.post(
        "/users",
        json={
            "username": "notify-valid-user",
            "password": "test-password",
            "invite_code": "NOTIFY-VALID",
            "notification_threshold": "3",
        },
    )

    assert response.status_code == 201
    assert response.get_json()["notification_threshold"] == "3"
    assert response.get_json()["notification_threshold_count"] == "3"
    assert get_user_by_username("notify-valid-user").notification_threshold_count == "3"


def test_user_registration_stores_threshold_count_one():
    client = app.test_client()
    gym_name = "Notification Count One Gym"

    create_gym(gym_name, 10, invite_code="NOTIFY-COUNT-ONE")

    response = client.post(
        "/users",
        json={
            "username": "notify-count-one-user",
            "password": "test-password",
            "invite_code": "NOTIFY-COUNT-ONE",
            "notification_threshold": "1",
        },
    )

    assert response.status_code == 201
    assert get_user_by_username("notify-count-one-user").notification_threshold_count == "1"


def test_user_registration_stores_threshold_count_at_max_capacity():
    client = app.test_client()
    gym_name = "Notification Count Max Gym"

    create_gym(gym_name, 10, invite_code="NOTIFY-COUNT-MAX")

    response = client.post(
        "/users",
        json={
            "username": "notify-count-max-user",
            "password": "test-password",
            "invite_code": "NOTIFY-COUNT-MAX",
            "notification_threshold": "10",
        },
    )

    assert response.status_code == 201
    assert get_user_by_username("notify-count-max-user").notification_threshold_count == "10"


def test_user_registration_rejects_invalid_notification_threshold():
    client = app.test_client()
    gym_name = "Notification Invalid Gym"

    create_gym(gym_name, 10, invite_code="NOTIFY-INVALID")

    response = client.post(
        "/users",
        json={
            "username": "notify-invalid-user",
            "password": "test-password",
            "invite_code": "NOTIFY-INVALID",
            "notification_threshold": "11",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid notification threshold"}
    assert get_user_by_username("notify-invalid-user") is None


def test_user_registration_rejects_zero_notification_threshold():
    client = app.test_client()
    gym_name = "Notification Zero Gym"

    create_gym(gym_name, 10, invite_code="NOTIFY-ZERO")

    response = client.post(
        "/users",
        json={
            "username": "notify-zero-user",
            "password": "test-password",
            "invite_code": "NOTIFY-ZERO",
            "notification_threshold": "0",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid notification threshold"}


def test_user_can_update_own_notification_preference(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notification Own Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    user_headers = auth_headers_for_gym(name, role="member")
    response = client.patch(
        "/me/notification-preference",
        json={"notification_threshold": "2"},
        headers=user_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["notification_threshold"] == "2"
    assert response.get_json()["notification_threshold_count"] == "2"


def test_user_can_update_notification_preference_to_40_people(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notification Forty Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 50},
        headers=auth_headers,
    )

    user_headers = auth_headers_for_gym(name, role="member")
    response = client.patch(
        "/me/notification-preference",
        json={"notification_threshold_count": "40"},
        headers=user_headers,
    )

    assert response.status_code == 200
    assert response.get_json()["notification_threshold_count"] == "40"


def test_update_notification_preference_rejects_invalid_value(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notification Patch Invalid Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    user_headers = auth_headers_for_gym(name, role="member")
    response = client.patch(
        "/me/notification-preference",
        json={"notification_threshold": "11"},
        headers=user_headers,
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid notification threshold"}


def test_notification_preference_requires_token():
    client = app.test_client()

    response = client.get("/me/notification-preference")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Missing token"}


def test_notification_preference_returns_updated_value(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notification Persisted Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    user_headers = auth_headers_for_gym(name, role="trainer")
    update_response = client.patch(
        "/me/notification-preference",
        json={"notification_threshold": "1"},
        headers=user_headers,
    )
    get_response = client.get(
        "/me/notification-preference",
        headers=user_headers,
    )

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    assert get_response.get_json()["notification_threshold"] == "1"
    assert get_response.get_json()["notification_threshold_count"] == "1"


def test_users_to_notify_ignores_off_threshold():
    gym_name = "Notify Off Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notify-off-user",
        "test-password",
        gym.id,
        notification_threshold="off",
    )

    users = get_users_to_notify(gym_name)

    assert users == []


def test_users_to_notify_returns_threshold_at_or_below_people_count():
    gym_name = "Notify Thirty Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notify-thirty-user",
        "test-password",
        gym.id,
        notification_threshold="3",
    )

    for _ in range(3):
        enter_gym(gym_name)

    users = get_users_to_notify(gym_name)

    assert users == [
        {
            "username": "notify-thirty-user",
            "notification_threshold": "3",
            "notification_threshold_count": "3",
            "current_count": 3,
        }
    ]


def test_users_to_notify_excludes_20_threshold_when_gym_is_at_30_percent():
    gym_name = "Notify Twenty Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notify-twenty-user",
        "test-password",
        gym.id,
        notification_threshold="2",
    )

    for _ in range(3):
        enter_gym(gym_name)

    users = get_users_to_notify(gym_name)

    assert users == []


def test_users_to_notify_excludes_users_from_other_gym():
    own_gym_name = "Notify Own Gym"
    other_gym_name = "Notify Other Gym"
    create_gym(own_gym_name, 10)
    create_gym(other_gym_name, 10)
    other_gym = get_gym_by_invite_code(other_gym_name)
    create_user(
        "notify-other-user",
        "test-password",
        other_gym.id,
        notification_threshold="5",
    )

    users = get_users_to_notify(own_gym_name)

    assert users == []


def test_member_cannot_get_notification_eligible_users(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notify Member Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.get(
        f"/gyms/{name}/notifications/eligible-users",
        headers=member_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_manager_can_get_notification_eligible_users_without_pending_matches(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Notify Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(
        "notify-manager-visible-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get(
        f"/gyms/{name}/notifications/eligible-users",
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert {
        "username": "notify-manager-visible-user",
        "notification_threshold": "5",
        "notification_threshold_count": "5",
        "current_count": 0,
    } in response.get_json()["eligible_users"]


def test_manager_can_get_notification_eligible_users(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notify Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get(
        f"/gyms/{name}/notifications/eligible-users",
        headers=manager_headers,
    )

    assert response.status_code == 200


def test_notification_is_created_when_user_is_below_threshold():
    gym_name = "Notification Create Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notification-create-user",
        "test-password",
        gym.id,
        notification_threshold="3",
    )

    notifications = generate_notifications_for_gym(gym_name)

    assert len(notifications) == 1
    assert notifications[0]["threshold"] == "3"
    assert notifications[0]["current_utilization_percent"] == 0
    assert "Notification Create Gym currently has 0 people" in notifications[0]["message"]

    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification is not None
    assert pending_notification.status == "pending"
    assert pending_notification.message == notifications[0]["message"]
    assert pending_notification.utilization == notifications[0]["current_utilization_percent"]


def test_get_pending_notifications_returns_pending_entries():
    gym_name = "Pending Service Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "pending-service-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )
    generate_notifications_for_gym(gym_name)

    pending_notifications = get_pending_notifications()

    assert len(pending_notifications) == 1
    assert pending_notifications[0]["status"] == "pending"
    assert pending_notifications[0]["error_message"] is None
    assert pending_notifications[0]["sent_at"] is None


def test_manager_can_get_pending_notifications_when_none_exist(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Pending Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(
        "pending-manager-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )
    generate_notifications_for_gym(name)

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/notifications/pending", headers=manager_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert len(body["pending_notifications"]) == 1
    assert body["pending_notifications"][0]["status"] == "pending"


def test_manager_can_get_pending_notifications(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Pending Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.get("/notifications/pending", headers=manager_headers)

    assert response.status_code == 200


def test_member_cannot_get_pending_notifications(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Pending Member Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.get("/notifications/pending", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_manager_can_get_push_dashboard_for_own_gym(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    own_gym_name = "Push Dashboard Own Gym"
    other_gym_name = "Push Dashboard Other Gym"

    client.post(
        "/gyms",
        json={"name": own_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    client.post(
        "/gyms",
        json={"name": other_gym_name, "max_capacity": 10},
        headers=auth_headers,
    )
    own_gym = get_gym_by_invite_code(own_gym_name)
    other_gym = get_gym_by_invite_code(other_gym_name)
    own_user = create_user("push-dashboard-own-user", "test-password", own_gym.id)
    other_user = create_user("push-dashboard-other-user", "test-password", other_gym.id)

    with SessionLocal() as session:
        session.add_all(
            [
                DeviceToken(user_id=own_user.id, token="own-token-1", platform="ios"),
                DeviceToken(user_id=own_user.id, token="own-token-2", platform="ios"),
                DeviceToken(user_id=own_user.id, token="own-token-3", platform="android"),
                DeviceToken(user_id=other_user.id, token="other-token", platform="ios"),
            ]
        )
        for status, count in (("pending", 2), ("sent", 10), ("failed", 1)):
            for index in range(count):
                session.add(
                    PendingNotification(
                        user_id=own_user.id,
                        gym_id=own_gym.id,
                        message=f"Own {status} notification {index}",
                        threshold="5",
                        utilization=3,
                        status=status,
                    )
                )
        for status in ("pending", "sent", "failed"):
            session.add(
                PendingNotification(
                    user_id=other_user.id,
                    gym_id=other_gym.id,
                    message=f"Other {status} notification",
                    threshold="5",
                    utilization=3,
                    status=status,
                )
            )
        session.commit()

    manager_headers = auth_headers_for_gym(own_gym_name, role="manager")
    response = client.get("/push/dashboard", headers=manager_headers)

    assert response.status_code == 200
    assert response.get_json() == {
        "registered_devices": 3,
        "pending_notifications": 2,
        "sent_notifications": 10,
        "failed_notifications": 1,
    }


def test_member_cannot_get_push_dashboard(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Push Dashboard Member Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.get("/push/dashboard", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_trainer_cannot_get_push_dashboard(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Push Dashboard Trainer Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    trainer_headers = auth_headers_for_gym(name, role="trainer")
    response = client.get("/push/dashboard", headers=trainer_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_mark_pending_notification_sent_sets_status_and_sent_at():
    gym_name = "Pending Sent Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("pending-sent-user", "test-password", gym.id)
    create_notification(user.id, gym.id, "Pending sent message", "50", 0.0)

    with SessionLocal() as session:
        pending_notification_id = session.query(PendingNotification).first().id

    marked_notification = mark_pending_notification_sent(pending_notification_id)

    assert marked_notification["status"] == "sent"
    assert marked_notification["sent_at"] is not None
    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification.status == "sent"
    assert pending_notification.sent_at is not None


def test_mark_pending_notification_failed_sets_status_and_error_message():
    gym_name = "Pending Failed Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("pending-failed-user", "test-password", gym.id)
    create_notification(user.id, gym.id, "Pending failed message", "50", 0.0)

    with SessionLocal() as session:
        pending_notification_id = session.query(PendingNotification).first().id

    marked_notification = mark_pending_notification_failed(
        pending_notification_id,
        "APNs token invalid",
    )

    assert marked_notification["status"] == "failed"
    assert marked_notification["error_message"] == "APNs token invalid"
    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification.status == "failed"
    assert pending_notification.error_message == "APNs token invalid"


def test_process_pending_notifications_marks_pending_notification_sent():
    gym_name = "Worker Sent Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-sent-user", "test-password", gym.id)
    create_notification(user.id, gym.id, "Worker sent message", "50", 0.0)

    result = process_pending_notifications()

    assert result == {"processed_count": 1, "failed_count": 0}
    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification.status == "sent"


def test_process_pending_notifications_sets_sent_at():
    gym_name = "Worker Sent At Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-sent-at-user", "test-password", gym.id)
    create_notification(user.id, gym.id, "Worker sent_at message", "50", 0.0)

    process_pending_notifications()

    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification.sent_at is not None


def test_push_delivery_service_finds_device_tokens_for_notification():
    gym_name = "Worker Device Token Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-device-token-user", "test-password", gym.id)
    register_device_token(user.username, "push-device-token", "ios")
    create_notification(user.id, gym.id, "Worker device token message", "50", 0.0)

    service = PushDeliveryService()
    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()
        device_tokens = service.get_device_tokens_for_notification(
            session,
            pending_notification,
        )

    assert len(device_tokens) == 1
    assert device_tokens[0].token == "push-device-token"
    assert device_tokens[0].platform == "ios"


def test_fake_push_provider_returns_success():
    class Device:
        token = "fake-device-token"
        platform = "ios"

    result = FakePushProvider().send(Device(), "Hello from UTZ")

    assert result == {
        "success": True,
        "token": "fake-device-token",
        "platform": "ios",
        "message": "Hello from UTZ",
    }


def test_firebase_push_provider_can_be_instantiated():
    provider = FirebasePushProvider()

    assert isinstance(provider, FirebasePushProvider)


class FakeFirebaseAdmin:
    def __init__(self):
        self._apps = {}
        self.initialized_with = None

    def initialize_app(self, credential):
        self.initialized_with = credential
        self._apps["default"] = object()
        return self._apps["default"]

    def get_app(self):
        return self._apps["default"]


class FakeFirebaseCredentials:
    def Certificate(self, credential_source):
        return {"credential_source": credential_source}


class FakeFirebaseMessaging:
    class Notification:
        def __init__(self, title, body):
            self.title = title
            self.body = body

    class Message:
        def __init__(self, notification, data, token):
            self.notification = notification
            self.data = data
            self.token = token

    def __init__(self, send_error=None):
        self.send_error = send_error
        self.sent_messages = []

    def send(self, message):
        if self.send_error:
            raise self.send_error

        self.sent_messages.append(message)
        return "firebase-message-id-1"


class FakeFirebaseUnregisteredError(Exception):
    pass


FakeFirebaseUnregisteredError.__name__ = "UnregisteredError"


def test_firebase_push_provider_sends_message_with_mocked_client(monkeypatch):
    class Device:
        token = "firebase-device-token"
        platform = "android"

    monkeypatch.setenv("FIREBASE_CREDENTIALS_PATH", "/tmp/firebase-service-account.json")
    firebase_admin = FakeFirebaseAdmin()
    messaging = FakeFirebaseMessaging()
    provider = FirebasePushProvider(
        firebase_admin_module=firebase_admin,
        credentials_module=FakeFirebaseCredentials(),
        messaging_module=messaging,
    )

    result = provider.send(Device(), "Hello from UTZ")

    assert result == {
        "success": True,
        "provider": "firebase",
        "token": "firebase-device-token",
        "platform": "android",
        "message": "Hello from UTZ",
        "firebase_message_id": "firebase-message-id-1",
    }
    assert firebase_admin.initialized_with == {
        "credential_source": "/tmp/firebase-service-account.json"
    }
    assert messaging.sent_messages[0].token == "firebase-device-token"
    assert messaging.sent_messages[0].notification.title == "UTZ Gym Alert"
    assert messaging.sent_messages[0].notification.body == "Hello from UTZ"


def test_firebase_push_provider_raises_clear_error_without_credentials(monkeypatch):
    class Device:
        token = "firebase-device-token"
        platform = "ios"

    monkeypatch.delenv("FIREBASE_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    provider = FirebasePushProvider(
        firebase_admin_module=FakeFirebaseAdmin(),
        credentials_module=FakeFirebaseCredentials(),
        messaging_module=FakeFirebaseMessaging(),
    )

    with pytest.raises(PushProviderConfigurationError, match="Firebase credentials"):
        provider.send(Device(), "Hello from UTZ")


def test_firebase_push_provider_wraps_send_errors(monkeypatch):
    class Device:
        token = "firebase-device-token"
        platform = "ios"

    monkeypatch.setenv("FIREBASE_CREDENTIALS_PATH", "/tmp/firebase-service-account.json")
    provider = FirebasePushProvider(
        firebase_admin_module=FakeFirebaseAdmin(),
        credentials_module=FakeFirebaseCredentials(),
        messaging_module=FakeFirebaseMessaging(send_error=RuntimeError("network down")),
    )

    with pytest.raises(PushProviderError, match="Firebase push failed"):
        provider.send(Device(), "Hello from UTZ")


def test_firebase_push_provider_detects_invalid_tokens(monkeypatch):
    class Device:
        token = "invalid-firebase-device-token"
        platform = "ios"

    monkeypatch.setenv("FIREBASE_CREDENTIALS_PATH", "/tmp/firebase-service-account.json")
    provider = FirebasePushProvider(
        firebase_admin_module=FakeFirebaseAdmin(),
        credentials_module=FakeFirebaseCredentials(),
        messaging_module=FakeFirebaseMessaging(send_error=FakeFirebaseUnregisteredError()),
    )

    with pytest.raises(InvalidFirebaseTokenError, match="Invalid Firebase device token"):
        provider.send(Device(), "Hello from UTZ")


def test_push_delivery_service_defaults_to_fake_push_provider(monkeypatch):
    monkeypatch.delenv("PUSH_PROVIDER", raising=False)

    service = PushDeliveryService()

    assert isinstance(service.push_provider, FakePushProvider)


def test_create_push_provider_defaults_to_fake_push_provider(monkeypatch):
    monkeypatch.delenv("PUSH_PROVIDER", raising=False)

    provider = create_push_provider()

    assert isinstance(provider, FakePushProvider)


def test_create_push_provider_uses_fake_push_provider():
    provider = create_push_provider("fake")

    assert isinstance(provider, FakePushProvider)


def test_create_push_provider_uses_firebase_push_provider():
    provider = create_push_provider("firebase")

    assert isinstance(provider, FirebasePushProvider)


def test_create_push_provider_uses_fake_push_provider_from_env(monkeypatch):
    monkeypatch.setenv("PUSH_PROVIDER", "fake")

    provider = create_push_provider()

    assert isinstance(provider, FakePushProvider)


def test_create_push_provider_uses_firebase_push_provider_from_env(monkeypatch):
    monkeypatch.setenv("PUSH_PROVIDER", "firebase")

    provider = create_push_provider()

    assert isinstance(provider, FirebasePushProvider)


def test_create_push_provider_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unsupported PUSH_PROVIDER"):
        create_push_provider("unknown")


def test_create_push_provider_rejects_unknown_provider_from_env(monkeypatch):
    monkeypatch.setenv("PUSH_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="Unsupported PUSH_PROVIDER"):
        create_push_provider()


def test_push_delivery_service_uses_push_provider():
    class RecordingPushProvider:
        def __init__(self):
            self.calls = []

        def send(self, device_token, message):
            self.calls.append((device_token.token, device_token.platform, message))
            return {"success": True}

    gym_name = "Worker Provider Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-provider-user", "test-password", gym.id)
    register_device_token(user.username, "provider-device-token", "android")
    create_notification(user.id, gym.id, "Worker provider message", "50", 0.0)

    provider = RecordingPushProvider()
    service = PushDeliveryService(push_provider=provider)
    result = service.process_pending_notifications()

    assert result == {"processed_count": 1, "failed_count": 0}
    assert provider.calls == [
        ("provider-device-token", "android", "Worker provider message")
    ]


def test_push_delivery_service_removes_invalid_firebase_tokens():
    class InvalidTokenProvider:
        def send(self, device_token, message):
            raise InvalidFirebaseTokenError("Invalid Firebase device token")

    gym_name = "Worker Invalid Firebase Token Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-invalid-token-user", "test-password", gym.id)
    register_device_token(user.username, "invalid-firebase-token", "ios")
    create_notification(user.id, gym.id, "Worker invalid token message", "50", 0.0)

    service = PushDeliveryService(push_provider=InvalidTokenProvider())
    result = service.process_pending_notifications()

    assert result == {"processed_count": 1, "failed_count": 0}
    with SessionLocal() as session:
        assert session.query(DeviceToken).count() == 0
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification.status == "sent"
    assert pending_notification.error_message is None


def test_process_pending_notifications_does_not_reprocess_sent_notifications():
    gym_name = "Worker Already Sent Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-already-sent-user", "test-password", gym.id)
    create_notification(user.id, gym.id, "Worker already sent message", "50", 0.0)

    first_result = process_pending_notifications()
    second_result = process_pending_notifications()

    assert first_result == {"processed_count": 1, "failed_count": 0}
    assert second_result == {"processed_count": 0, "failed_count": 0}


def test_process_pending_notifications_marks_failed_when_delivery_raises():
    gym_name = "Worker Failed Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    user = create_user("worker-failed-user", "test-password", gym.id)
    create_notification(user.id, gym.id, "Worker failed message", "50", 0.0)

    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()
        pending_notification.error_message = "Simulated push failure"
        session.commit()

    result = process_pending_notifications()

    assert result == {"processed_count": 0, "failed_count": 1}
    with SessionLocal() as session:
        pending_notification = session.query(PendingNotification).first()

    assert pending_notification.status == "failed"
    assert pending_notification.error_message == "Simulated push failure"


def test_manager_can_process_pending_notifications(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Worker Manager Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(
        "worker-manager-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )
    generate_notifications_for_gym(name)

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.post("/notifications/process", headers=manager_headers)

    assert response.status_code == 200
    assert response.get_json() == {"processed_count": 1, "failed_count": 0}


def test_member_cannot_process_pending_notifications(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Worker Member Block Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.post("/notifications/process", headers=member_headers)

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_notification_is_not_created_when_threshold_is_off():
    gym_name = "Notification Off Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notification-off-user",
        "test-password",
        gym.id,
        notification_threshold="off",
    )

    notifications = generate_notifications_for_gym(gym_name)

    assert notifications == []


def test_notification_is_not_created_for_user_from_other_gym():
    own_gym_name = "Notification Own Gym"
    other_gym_name = "Notification Other Gym"
    create_gym(own_gym_name, 10)
    create_gym(other_gym_name, 10)
    other_gym = get_gym_by_invite_code(other_gym_name)
    create_user(
        "notification-other-user",
        "test-password",
        other_gym.id,
        notification_threshold="5",
    )

    notifications = generate_notifications_for_gym(own_gym_name)

    assert notifications == []


def test_notification_antispam_blocks_second_notification_within_two_hours():
    gym_name = "Notification Antispam Gym"
    created_at = datetime(2026, 6, 6, 10, 0)
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notification-antispam-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )

    first_notifications = generate_notifications_for_gym(gym_name, created_at=created_at)
    second_notifications = generate_notifications_for_gym(
        gym_name,
        created_at=created_at + timedelta(hours=1),
    )

    assert len(first_notifications) == 1
    assert second_notifications == []
    with SessionLocal() as session:
        assert session.query(Notification).count() == 1


def test_notification_antispam_allows_notification_after_more_than_two_hours():
    gym_name = "Notification Antispam Later Gym"
    created_at = datetime(2026, 6, 6, 10, 0)
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "notification-antispam-later-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )

    first_notifications = generate_notifications_for_gym(gym_name, created_at=created_at)
    second_notifications = generate_notifications_for_gym(
        gym_name,
        created_at=created_at + timedelta(hours=2, minutes=1),
    )

    assert len(first_notifications) == 1
    assert len(second_notifications) == 1
    with SessionLocal() as session:
        assert session.query(Notification).count() == 2


def set_gym_current_count(gym_name, current_count):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=gym_name).first()
        gym.current_count = current_count
        session.commit()


def test_automatic_notification_is_created_when_leave_drops_below_threshold():
    gym_name = "Auto Notification Below Gym"
    create_gym(gym_name, 100)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "auto-below-user",
        "test-password",
        gym.id,
        notification_threshold="40",
    )
    set_gym_current_count(gym_name, 40)

    result = leave_gym(gym_name)

    assert result["current"] == 39
    with SessionLocal() as session:
        notification = session.query(Notification).one()
        pending_notification = session.query(PendingNotification).one()

    assert notification.threshold == "40"
    assert notification.current_utilization_percent == 39
    assert "Auto Notification Below Gym currently has 39 people" in notification.message
    assert pending_notification.status == "pending"
    assert pending_notification.message == notification.message


def test_automatic_notification_is_created_after_enter_when_still_under_threshold():
    gym_name = "Auto Notification Enter Gym"
    create_gym(gym_name, 100)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "auto-enter-user",
        "test-password",
        gym.id,
        notification_threshold="40",
    )
    set_gym_current_count(gym_name, 38)

    result = enter_gym(gym_name)

    assert result["current"] == 39
    with SessionLocal() as session:
        notification = session.query(Notification).one()

    assert notification.threshold == "40"
    assert notification.current_utilization_percent == 39


def test_automatic_notification_is_not_created_when_gym_stays_above_threshold():
    gym_name = "Auto Notification Above Gym"
    create_gym(gym_name, 100)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "auto-above-user",
        "test-password",
        gym.id,
        notification_threshold="40",
    )
    set_gym_current_count(gym_name, 42)

    result = leave_gym(gym_name)

    assert result["current"] == 41
    with SessionLocal() as session:
        assert session.query(Notification).count() == 0
        assert session.query(PendingNotification).count() == 0


def test_automatic_notification_is_not_created_when_notifications_are_off():
    gym_name = "Auto Notification Off Gym"
    create_gym(gym_name, 100)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "auto-off-user",
        "test-password",
        gym.id,
        notification_threshold="off",
    )
    set_gym_current_count(gym_name, 40)

    leave_gym(gym_name)

    with SessionLocal() as session:
        assert session.query(Notification).count() == 0
        assert session.query(PendingNotification).count() == 0


def test_automatic_notification_antispam_blocks_second_notification():
    gym_name = "Auto Notification Antispam Gym"
    create_gym(gym_name, 100)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "auto-antispam-user",
        "test-password",
        gym.id,
        notification_threshold="40",
    )
    set_gym_current_count(gym_name, 40)

    leave_gym(gym_name)
    set_gym_current_count(gym_name, 40)
    leave_gym(gym_name)

    with SessionLocal() as session:
        assert session.query(Notification).count() == 1
        assert session.query(PendingNotification).count() == 1


def test_automatic_notification_only_matches_users_in_same_gym_by_threshold():
    gym_name = "Auto Notification Multi User Gym"
    create_gym(gym_name, 100)
    gym = get_gym_by_invite_code(gym_name)
    create_user(
        "auto-too-low-threshold-user",
        "test-password",
        gym.id,
        notification_threshold="38",
    )
    matching_user = create_user(
        "auto-matching-threshold-user",
        "test-password",
        gym.id,
        notification_threshold="40",
    )
    higher_matching_user = create_user(
        "auto-higher-threshold-user",
        "test-password",
        gym.id,
        notification_threshold="50",
    )
    set_gym_current_count(gym_name, 40)

    leave_gym(gym_name)

    with SessionLocal() as session:
        notifications = (
            session.query(Notification)
            .order_by(Notification.user_id)
            .all()
        )

    assert {notification.user_id for notification in notifications} == {
        matching_user.id,
        higher_matching_user.id,
    }


def test_automatic_notification_does_not_notify_users_from_other_gyms():
    own_gym_name = "Auto Notification Own Gym"
    other_gym_name = "Auto Notification Other Gym"
    create_gym(own_gym_name, 100)
    create_gym(other_gym_name, 100)
    own_gym = get_gym_by_invite_code(own_gym_name)
    other_gym = get_gym_by_invite_code(other_gym_name)
    own_user = create_user(
        "auto-own-gym-user",
        "test-password",
        own_gym.id,
        notification_threshold="40",
    )
    create_user(
        "auto-other-gym-user",
        "test-password",
        other_gym.id,
        notification_threshold="40",
    )
    set_gym_current_count(own_gym_name, 40)

    leave_gym(own_gym_name)

    with SessionLocal() as session:
        notifications = session.query(Notification).all()

    assert len(notifications) == 1
    assert notifications[0].user_id == own_user.id
    assert notifications[0].gym_id == own_gym.id


def test_user_sees_only_own_notifications():
    client = app.test_client()
    gym_name = "Notification Own View Gym"
    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    first_user = create_user(
        "notification-own-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )
    second_user = create_user(
        "notification-other-visible-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )
    create_notification(
        first_user.id,
        gym.id,
        "First user notification",
        "50",
        0.0,
    )
    create_notification(
        second_user.id,
        gym.id,
        "Second user notification",
        "50",
        0.0,
    )
    token = encode_token(
        {
            "username": first_user.username,
            "gym_id": first_user.gym_id,
            "role": first_user.role,
        }
    )

    response = client.get(
        "/me/notifications",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    messages = [
        notification["message"]
        for notification in response.get_json()["notifications"]
    ]
    assert messages == ["First user notification"]


def test_notifications_requires_token():
    client = app.test_client()

    response = client.get("/me/notifications")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Missing token"}


def test_member_cannot_generate_notifications(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notification Member Generate Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    member_headers = auth_headers_for_gym(name, role="member")
    response = client.post(
        f"/gyms/{name}/notifications/generate",
        headers=member_headers,
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Manager access required"}


def test_manager_can_generate_notifications_when_no_users_match(
    auth_headers,
    auth_headers_for_gym,
):
    client = app.test_client()
    name = "Notification Manager Generate Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )
    gym = get_gym_by_invite_code(name)
    create_user(
        "notification-manager-generate-user",
        "test-password",
        gym.id,
        notification_threshold="5",
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.post(
        f"/gyms/{name}/notifications/generate",
        headers=manager_headers,
    )

    assert response.status_code == 201
    assert response.get_json()["created"] == 1


def test_manager_can_generate_notifications(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Notification Manager Generate Gym"

    client.post(
        "/gyms",
        json={"name": name, "max_capacity": 10},
        headers=auth_headers,
    )

    manager_headers = auth_headers_for_gym(name, role="manager")
    response = client.post(
        f"/gyms/{name}/notifications/generate",
        headers=manager_headers,
    )

    assert response.status_code == 201


def test_enter_full_gym_through_api_returns_409(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "Tiny API Gym"

    create = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 1},
        headers=auth_headers,
    )
    assert create.status_code == 201

    gym_auth_headers = auth_headers_for_gym(name)
    first_enter = client.post(f"/gyms/{name}/enter", headers=gym_auth_headers)
    assert first_enter.status_code == 200

    second_enter = client.post(f"/gyms/{name}/enter", headers=gym_auth_headers)

    assert second_enter.status_code == 409
    assert second_enter.get_json() == {"error": "Gym is full"}


def test_gym_history_endpoint_returns_occupancy_events(auth_headers, auth_headers_for_gym):
    client = app.test_client()
    name = "History API Gym"

    create = client.post(
        "/gyms",
        json={"name": name, "max_capacity": 5},
        headers=auth_headers,
    )
    assert create.status_code == 201

    gym_auth_headers = auth_headers_for_gym(name)
    first_enter = client.post(f"/gyms/{name}/enter", headers=gym_auth_headers)
    assert first_enter.status_code == 200

    second_enter = client.post(f"/gyms/{name}/enter", headers=gym_auth_headers)
    assert second_enter.status_code == 200

    leave = client.post(f"/gyms/{name}/leave", headers=gym_auth_headers)
    assert leave.status_code == 200

    response = client.get(f"/gyms/{name}/history")

    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, list)
    assert len(body) >= 3

    for event in body:
        assert "current_count" in event
        assert "timestamp" in event


def test_unknown_gym_history_returns_404():
    client = app.test_client()

    response = client.get("/gyms/UnknownGym/history")

    assert response.status_code == 404


def test_best_training_time_returns_lowest_average_hour():
    client = app.test_client()
    name = "Best Time Gym"
    create_gym(name, 40)

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()
        session.add_all(
            [
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=30,
                    timestamp=datetime(2026, 5, 26, 9, 10),
                ),
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=20,
                    timestamp=datetime(2026, 5, 26, 9, 40),
                ),
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=8,
                    timestamp=datetime(2026, 5, 26, 14, 5),
                ),
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=12,
                    timestamp=datetime(2026, 5, 26, 14, 35),
                ),
            ]
        )
        session.commit()

    response = client.get(f"/gyms/{name}/best-time")

    assert response.status_code == 200
    assert response.get_json() == {
        "best_hour": 14,
        "label": "14:00 - 15:00",
        "average_occupancy": 10,
    }


def test_best_training_time_returns_fallback_without_enough_data():
    client = app.test_client()
    name = "Sparse Best Time Gym"

    create_gym(name, 40)

    response = client.get(f"/gyms/{name}/best-time")

    assert response.status_code == 200
    assert response.get_json() == {"message": "Not enough data"}


def test_best_training_time_unknown_gym_returns_404():
    client = app.test_client()

    response = client.get("/gyms/UnknownGym/best-time")

    assert response.status_code == 404


def test_peak_hour_returns_highest_average_hour():
    client = app.test_client()
    name = "Peak Hour Gym"
    create_gym(name, 40)

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()
        session.add_all(
            [
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=12,
                    timestamp=datetime(2026, 5, 26, 8, 10),
                ),
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=18,
                    timestamp=datetime(2026, 5, 26, 9, 40),
                ),
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=71,
                    timestamp=datetime(2026, 5, 26, 18, 5),
                ),
                OccupancyEvent(
                    gym_id=gym.id,
                    current_count=55,
                    timestamp=datetime(2026, 5, 26, 19, 35),
                ),
            ]
        )
        session.commit()

    response = client.get(f"/gyms/{name}/peak-hour")

    assert response.status_code == 200
    assert response.get_json() == {
        "peak_hour": 18,
        "label": "18:00 - 19:00",
        "average_occupancy": 71,
    }


def test_peak_hour_returns_fallback_without_enough_data():
    client = app.test_client()
    name = "Sparse Peak Hour Gym"

    create_gym(name, 40)

    response = client.get(f"/gyms/{name}/peak-hour")

    assert response.status_code == 200
    assert response.get_json() == {"message": "Not enough data"}


def test_peak_hour_unknown_gym_returns_404():
    client = app.test_client()

    response = client.get("/gyms/UnknownGym/peak-hour")

    assert response.status_code == 404


def test_gym_is_persisted_in_sqlite_after_create():
    create_gym("Database Gym", 40)

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name="Database Gym").first()

    assert gym is not None
    assert gym.current_count == 0
    assert gym.max_capacity == 40


def test_login_returns_user_role():
    client = app.test_client()
    gym_name = "Login Role Gym"
    username = "login-role-manager"

    create_gym(gym_name, 10)
    gym = get_gym_by_invite_code(gym_name)
    create_user(username, "test-password", gym.id, role="manager")

    response = client.post(
        "/login",
        json={"username": username, "password": "test-password"},
    )

    assert response.status_code == 200
    assert response.get_json()["role"] == "manager"


def test_login_page_renders_login_form():
    client = app.test_client()

    response = client.get("/login-page")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="login-form"' in body
    assert 'name="username"' in body
    assert 'name="password"' in body


def test_dashboard_contains_member_visible_sections():
    client = app.test_client()
    name = "Role Dashboard Member Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="live-status"' in body
    assert 'id="best-time-value"' in body
    assert 'id="peak-hour-value"' in body
    assert 'id="notification-settings"' in body
    assert 'id="notifications"' in body


def test_dashboard_marks_manager_sections_for_role_hiding():
    client = app.test_client()
    name = "Role Dashboard Manager Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="manager-settings" data-role-section="manager"' in body
    assert 'id="member-access" data-role-section="manager"' in body
    assert 'id="push-dashboard" data-role-section="manager"' in body
    assert 'id="device-management" data-role-section="manager"' in body
    assert 'setSectionVisibility(\'[data-role-section="manager"]\', canManageGym)' in body
    assert 'setSectionVisibility(\'.sidebar-link[href="#push-dashboard"]\', canManageGym)' in body
    assert 'setSectionVisibility(\'.sidebar-link[href="#device-management"]\', canManageGym)' in body


def test_dashboard_uses_jwt_role_for_frontend_permissions():
    client = app.test_client()
    name = "Role Dashboard Jwt Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "const tokenRole = decodeJwtPayload(token).role;" in body
    assert 'canManageGym: ["manager"].includes(currentRole)' in body
    assert 'canViewMembers: ["manager", "trainer"].includes(currentRole)' in body
    assert "document.body.dataset.role = currentRole;" in body


def test_dashboard_contains_manager_push_dashboard_ui():
    client = app.test_client()
    name = "Push Dashboard UI Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Push Dashboard" in body
    assert "Registered Devices" in body
    assert "Pending Pushes" in body
    assert "Sent Pushes" in body
    assert "Failed Pushes" in body
    assert 'id="push-registered-devices"' in body
    assert 'id="push-pending-notifications"' in body
    assert 'id="push-sent-notifications"' in body
    assert 'id="push-failed-notifications"' in body


def test_dashboard_loads_push_dashboard_with_jwt():
    client = app.test_client()
    name = "Push Dashboard Fetch Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'fetch("/push/dashboard"' in body
    assert "Authorization: `Bearer ${token}`" in body
    assert "renderPushDashboard(data)" in body
    assert "loadPushDashboard();" in body
    assert "Push dashboard unavailable" in body


def test_dashboard_contains_manager_device_management_ui():
    client = app.test_client()
    name = "Device Management UI Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Device Management" in body
    assert 'id="devices-total"' in body
    assert "Total Devices: 0" in body
    assert "Device ID:" in body
    assert "Platform:" in body
    assert "Last Seen:" in body
    assert "Push Enabled:" in body
    assert "Health:" in body
    assert "Status:" in body
    assert "No devices registered yet" in body
    assert "Devices unavailable" in body


def test_dashboard_loads_device_management_only_for_managers():
    client = app.test_client()
    name = "Device Management Fetch Gym"
    create_gym(name, 10)

    response = client.get(f"/dashboard/{name}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'fetch("/devices"' in body
    assert "Authorization: `Bearer ${token}`" in body
    assert "if (!canManageGym)" in body
    assert "loadDevices();" in body
