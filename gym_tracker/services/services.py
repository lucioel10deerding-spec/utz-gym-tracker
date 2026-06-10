from datetime import datetime, timedelta, timezone

import bcrypt
import secrets
import string

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from gym_tracker.auth.helpers import normalize_role
from gym_tracker.services.push_delivery import PushDeliveryService

from ..adapters.database import SessionLocal
from ..domain.models import (
    ActivityLog,
    DeviceToken,
    Gym,
    GymFullError,
    Notification,
    OccupancyEvent,
    PendingNotification,
    User,
)


class DuplicateGymError(Exception):
    pass


class RemoveMemberError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class UpdateMemberRoleError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class UserActivationError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class DeviceRegistrationError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class NotificationPreferenceError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


ALLOWED_USER_ROLES = {"member", "trainer", "manager"}
ROLE_UPDATE_TARGETS = {"member", "trainer"}
ALLOWED_DEVICE_PLATFORMS = {"ios", "android", "unknown"}
ALLOWED_CLIENT_DEVICE_PLATFORMS = {"ios", "android"}
DEFAULT_DEVICE_PLATFORM = "unknown"
NOTIFICATION_COOLDOWN = timedelta(hours=2)
DEVICE_HEALTH_ACTIVE_WINDOW = timedelta(days=7)
UNSET = object()


def utc_now():
    return datetime.now(timezone.utc)


def align_datetime_timezone(value, reference):
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)

    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)

    return value


def create_gym(name: str, max_capacity: int, invite_code: str | None = None):
    with SessionLocal() as session:
        existing_gym = session.query(Gym).filter_by(name=name).first()
        if existing_gym is not None:
            raise DuplicateGymError("Gym already exists")

        gym = Gym(
            name=name,
            max_capacity=max_capacity,
            invite_code=invite_code or name,
            current_count=0,
        )
        session.add(gym)

        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise DuplicateGymError("Gym already exists") from error

        return {
            "current": gym.current_count,
            "max": gym.max_capacity,
            "status": gym.status(),
        }


def create_user(username, password, gym_id, role="member", notification_threshold="off"):
    notification_threshold_count = validate_notification_threshold_count(
        gym_id,
        notification_threshold,
    )

    role = normalize_role(role)

    with SessionLocal() as session:
        hashed_password = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")
        user = User(
            username=username,
            password=hashed_password,
            gym_id=gym_id,
            role=role,
            notification_threshold=notification_threshold_count,
            notification_threshold_count=notification_threshold_count,
        )
        session.add(user)
        session.commit()

        return user


def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


def get_user_by_username(username):
    with SessionLocal() as session:
        return session.query(User).filter_by(username=username).first()


def get_notification_preference(username):
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise NotificationPreferenceError("User not found", 404)

        return {
            "username": user.username,
            "notification_threshold": user.notification_threshold_count,
            "notification_threshold_count": user.notification_threshold_count,
        }


def update_notification_preference(username, notification_threshold):
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise NotificationPreferenceError("User not found", 404)

        notification_threshold_count = validate_notification_threshold_count(
            user.gym_id,
            notification_threshold,
        )
        user.notification_threshold = notification_threshold_count
        user.notification_threshold_count = notification_threshold_count
        session.commit()

        return {
            "username": user.username,
            "notification_threshold": user.notification_threshold_count,
            "notification_threshold_count": user.notification_threshold_count,
        }


def validate_notification_threshold_count(gym_id, notification_threshold_count):
    if notification_threshold_count == "off":
        return "off"

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(id=gym_id).first()

        if gym is None:
            raise NotificationPreferenceError("Gym not found", 404)

        try:
            threshold_count = int(notification_threshold_count)
        except (TypeError, ValueError):
            raise NotificationPreferenceError("Invalid notification threshold", 400)

        if threshold_count < 1 or threshold_count > gym.max_capacity:
            raise NotificationPreferenceError("Invalid notification threshold", 400)

        return str(threshold_count)


def get_users_to_notify(gym_name):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=gym_name).first()

        if gym is None:
            return None

        users = (
            session.query(User)
            .filter(User.gym_id == gym.id)
            .filter(User.notification_threshold_count != "off")
            .order_by(User.username)
            .all()
        )

        return [
            {
                "username": user.username,
                "notification_threshold": user.notification_threshold_count,
                "notification_threshold_count": user.notification_threshold_count,
                "current_count": gym.current_count,
            }
            for user in users
            if gym.current_count <= int(user.notification_threshold_count)
        ]


def serialize_notification(notification):
    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "gym_id": notification.gym_id,
        "message": notification.message,
        "threshold": notification.threshold,
        "current_utilization_percent": notification.current_utilization_percent,
        "created_at": notification.created_at.isoformat(),
        "is_read": notification.is_read,
    }


def serialize_pending_notification(notification):
    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "gym_id": notification.gym_id,
        "message": notification.message,
        "threshold": notification.threshold,
        "utilization": notification.utilization,
        "status": notification.status,
        "created_at": notification.created_at.isoformat(),
        "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
        "error_message": notification.error_message,
    }


def add_pending_notification(
    session,
    user_id,
    gym_id,
    message,
    threshold,
    utilization,
    created_at,
):
    pending_notification = PendingNotification(
        user_id=user_id,
        gym_id=gym_id,
        message=message,
        threshold=threshold,
        utilization=utilization,
        status="pending",
        created_at=created_at,
    )
    session.add(pending_notification)

    return pending_notification


def serialize_device_token(device_token):
    return {
        "id": device_token.id,
        "user_id": device_token.user_id,
        "token": device_token.token,
        "device_name": device_token.device_name,
        "platform": device_token.platform,
        "created_at": device_token.created_at.isoformat(),
        "last_seen": device_token.last_seen.isoformat() if device_token.last_seen else None,
        "push_enabled": device_token.push_enabled,
    }


def normalize_optional_device_name(device_name):
    if device_name is None:
        return None

    device_name = device_name.strip()

    return device_name or None


def get_neutral_device_name(platform):
    if platform == "ios":
        return "iOS Device"
    if platform == "android":
        return "Android Device"

    return "Unknown Device"


def get_device_display_name(device_token):
    return device_token.device_name or get_neutral_device_name(device_token.platform)


def get_device_health(device_token, reference_time=None):
    if device_token.last_seen is None:
        return "UNKNOWN"

    reference_time = reference_time or utc_now()
    last_seen = align_datetime_timezone(device_token.last_seen, reference_time)

    if reference_time - last_seen <= DEVICE_HEALTH_ACTIVE_WINDOW:
        return "ACTIVE"

    return "STALE"


def serialize_mobile_device(device_token):
    return {
        "id": device_token.id,
        "device_display_name": get_device_display_name(device_token),
        "platform": device_token.platform,
        "last_seen": device_token.last_seen.isoformat() if device_token.last_seen else None,
        "push_enabled": device_token.push_enabled,
        "status": "active",
        "device_health": get_device_health(device_token),
    }


def preview_device_token(token):
    if len(token) <= 4:
        return f"••••{token}"

    if len(token) <= 8:
        return f"••••{token[-4:]}"

    return f"{token[:6]}...{token[-4:]}"


def register_device_token(
    username,
    token,
    platform=None,
    device_name=UNSET,
    push_enabled=None,
):
    if platform is not None and platform not in ALLOWED_CLIENT_DEVICE_PLATFORMS:
        raise DeviceRegistrationError("Invalid platform", 400)
    if push_enabled is not None and not isinstance(push_enabled, bool):
        raise DeviceRegistrationError("Invalid push_enabled", 400)

    if device_name is not UNSET:
        device_name = normalize_optional_device_name(device_name)

    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise DeviceRegistrationError("User not found", 404)

        device_token = (
            session.query(DeviceToken)
            .filter_by(user_id=user.id, token=token)
            .first()
        )

        if device_token is None:
            device_token = DeviceToken(
                user_id=user.id,
                token=token,
                platform=platform or DEFAULT_DEVICE_PLATFORM,
                device_name=None if device_name is UNSET else device_name,
                last_seen=utc_now(),
                push_enabled=True if push_enabled is None else push_enabled,
            )
            session.add(device_token)
        else:
            if platform is not None:
                device_token.platform = platform
            if device_name is not UNSET:
                device_token.device_name = device_name
            device_token.last_seen = utc_now()
            if push_enabled is not None:
                device_token.push_enabled = push_enabled

        session.commit()

        return serialize_device_token(device_token)


def register_mobile_device(
    username,
    token,
    platform=None,
    device_name=UNSET,
    push_enabled=None,
):
    device_token = register_device_token(
        username=username,
        token=token,
        platform=platform,
        device_name=device_name,
        push_enabled=push_enabled,
    )

    with SessionLocal() as session:
        stored_device_token = session.query(DeviceToken).filter_by(id=device_token["id"]).first()

        return serialize_mobile_device(stored_device_token)


def update_own_device(
    username,
    token,
    device_name=UNSET,
    push_enabled=None,
):
    if push_enabled is not None and not isinstance(push_enabled, bool):
        raise DeviceRegistrationError("Invalid push_enabled", 400)

    if device_name is not UNSET:
        device_name = normalize_optional_device_name(device_name)

    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise DeviceRegistrationError("User not found", 404)

        device_token = (
            session.query(DeviceToken)
            .filter_by(user_id=user.id, token=token)
            .first()
        )

        if device_token is None:
            raise DeviceRegistrationError("Device not found", 404)

        if device_name is not UNSET:
            device_token.device_name = device_name
        if push_enabled is not None:
            device_token.push_enabled = push_enabled
        device_token.last_seen = utc_now()

        session.commit()

        return serialize_mobile_device(device_token)


def delete_own_device(username, token):
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise DeviceRegistrationError("User not found", 404)

        device_token = (
            session.query(DeviceToken)
            .filter_by(user_id=user.id, token=token)
            .first()
        )

        if device_token is None:
            raise DeviceRegistrationError("Device not found", 404)

        session.delete(device_token)
        session.commit()

        return {"message": "Device removed"}


def get_user_devices(username):
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise DeviceRegistrationError("User not found", 404)

        device_tokens = (
            session.query(DeviceToken)
            .filter_by(user_id=user.id)
            .order_by(DeviceToken.created_at.desc(), DeviceToken.id.desc())
            .all()
        )

        return [serialize_device_token(device_token) for device_token in device_tokens]


def serialize_gym_device(device_token):
    return {
        "id": device_token.id,
        "device_token_preview": preview_device_token(device_token.token),
        "device_name": device_token.device_name,
        "device_display_name": get_device_display_name(device_token),
        "platform": device_token.platform,
        "last_seen": device_token.last_seen.isoformat() if device_token.last_seen else None,
        "push_enabled": device_token.push_enabled,
        "status": "active",
        "device_health": get_device_health(device_token),
    }


def get_gym_devices(gym_id, session):
    device_tokens = (
        session.query(DeviceToken)
        .join(User, DeviceToken.user_id == User.id)
        .filter(User.gym_id == gym_id)
        .order_by(DeviceToken.created_at.desc(), DeviceToken.id.desc())
        .all()
    )

    return [serialize_gym_device(device_token) for device_token in device_tokens]


def get_push_dashboard_stats(gym_id, session):
    notification_counts = dict(
        session.query(PendingNotification.status, func.count(PendingNotification.id))
        .filter(PendingNotification.gym_id == gym_id)
        .filter(PendingNotification.status.in_(("pending", "sent", "failed")))
        .group_by(PendingNotification.status)
        .all()
    )
    registered_devices = (
        session.query(func.count(DeviceToken.id))
        .join(User, DeviceToken.user_id == User.id)
        .filter(User.gym_id == gym_id)
        .scalar()
    )

    return {
        "registered_devices": registered_devices,
        "pending_notifications": notification_counts.get("pending", 0),
        "sent_notifications": notification_counts.get("sent", 0),
        "failed_notifications": notification_counts.get("failed", 0),
    }


def create_notification(
    user_id,
    gym_id,
    message,
    threshold,
    current_utilization_percent,
    created_at=None,
):
    with SessionLocal() as session:
        created_at = created_at or utc_now()
        notification = Notification(
            user_id=user_id,
            gym_id=gym_id,
            message=message,
            threshold=threshold,
            current_utilization_percent=current_utilization_percent,
            created_at=created_at,
            is_read=False,
        )
        session.add(notification)
        add_pending_notification(
            session=session,
            user_id=user_id,
            gym_id=gym_id,
            message=message,
            threshold=threshold,
            utilization=current_utilization_percent,
            created_at=created_at,
        )
        session.commit()

        return serialize_notification(notification)


def get_pending_notifications():
    with SessionLocal() as session:
        notifications = (
            session.query(PendingNotification)
            .filter_by(status="pending")
            .order_by(PendingNotification.created_at)
            .all()
        )

        return [
            serialize_pending_notification(notification)
            for notification in notifications
        ]


def mark_pending_notification_sent(notification_id):
    with SessionLocal() as session:
        notification = session.query(PendingNotification).filter_by(id=notification_id).first()

        if notification is None:
            return None

        notification.status = "sent"
        notification.sent_at = utc_now()
        session.commit()

        return serialize_pending_notification(notification)


def mark_pending_notification_failed(notification_id, error_message):
    with SessionLocal() as session:
        notification = session.query(PendingNotification).filter_by(id=notification_id).first()

        if notification is None:
            return None

        notification.status = "failed"
        notification.error_message = error_message
        session.commit()

        return serialize_pending_notification(notification)


def process_pending_notifications():
    return PushDeliveryService().process_pending_notifications()


def get_user_notifications(username):
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            return []

        notifications = (
            session.query(Notification)
            .filter_by(user_id=user.id)
            .order_by(Notification.created_at.desc())
            .all()
        )

        return [serialize_notification(notification) for notification in notifications]


def generate_notifications_for_gym(gym_name, created_at=None):
    created_at = created_at or utc_now()
    eligible_users = get_users_to_notify(gym_name)

    if eligible_users is None:
        return None

    created_notifications = []

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=gym_name).first()

        for eligible_user in eligible_users:
            user = session.query(User).filter_by(username=eligible_user["username"]).first()

            if user is None or user.gym_id != gym.id:
                continue

            last_notification = (
                session.query(Notification)
                .filter_by(user_id=user.id)
                .order_by(Notification.created_at.desc())
                .first()
            )
            last_notification_created_at = (
                align_datetime_timezone(last_notification.created_at, created_at)
                if last_notification is not None
                else None
            )

            if (
                last_notification is not None
                and created_at - last_notification_created_at < NOTIFICATION_COOLDOWN
            ):
                continue

            current_count = eligible_user["current_count"]
            threshold = eligible_user["notification_threshold_count"]
            message = (
                f"{gym.name} currently has {current_count} people. "
                f"This is at or below your {threshold} people threshold."
            )
            notification = Notification(
                user_id=user.id,
                gym_id=gym.id,
                message=message,
                threshold=threshold,
                current_utilization_percent=current_count,
                created_at=created_at,
                is_read=False,
            )
            session.add(notification)
            add_pending_notification(
                session=session,
                user_id=user.id,
                gym_id=gym.id,
                message=message,
                threshold=threshold,
                utilization=current_count,
                created_at=created_at,
            )
            session.flush()
            created_notifications.append(serialize_notification(notification))

        session.commit()

    return created_notifications


def generate_notifications_after_occupancy_change(gym_name, previous_count, current_count):
    if previous_count == current_count:
        return []

    return generate_notifications_for_gym(gym_name)


def enter_gym(name: str):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        previous_count = gym.current_count
        gym.enter()
        event = OccupancyEvent(
            gym_id=gym.id,
            current_count=gym.current_count,
        )
        session.add(event)

        session.commit()

        result = {
            "id": gym.id,
            "current": gym.current_count,
            "max": gym.max_capacity,
            "status": gym.status(),
        }

    generate_notifications_after_occupancy_change(name, previous_count, result["current"])

    return result


def leave_gym(name: str):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        previous_count = gym.current_count
        gym.leave()
        event = OccupancyEvent(
            gym_id=gym.id,
            current_count=gym.current_count,
        )
        session.add(event)

        session.commit()

        result = {
            "id": gym.id,
            "current": gym.current_count,
            "max": gym.max_capacity,
            "status": gym.status(),
        }

    generate_notifications_after_occupancy_change(name, previous_count, result["current"])

    return result


def get_capacity(name: str):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        return {
            "id": gym.id,
            "current": gym.current_count,
            "max": gym.max_capacity,
            "status": gym.status(),
        }


def update_gym_settings(
    name: str,
    max_capacity=None,
    logo_url=None,
    primary_color=None,
):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        if max_capacity is not None:
            gym.max_capacity = max_capacity
        if logo_url is not None:
            gym.logo_url = logo_url
        if primary_color is not None:
            gym.primary_color = primary_color

        session.commit()

        return {
            "name": gym.name,
            "max_capacity": gym.max_capacity,
            "logo_url": gym.logo_url,
            "primary_color": gym.primary_color,
        }


def generate_gym_invite_code(name: str):
    alphabet = string.ascii_uppercase + string.digits

    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        prefix = "".join(character for character in gym.name.upper() if character.isalnum())[:8]
        if not prefix:
            prefix = "GYM"

        for _ in range(20):
            random_code = "".join(secrets.choice(alphabet) for _ in range(6))
            invite_code = f"{prefix}-{random_code}"
            existing_gym = session.query(Gym).filter_by(invite_code=invite_code).first()

            if existing_gym is None:
                gym.invite_code = invite_code
                session.commit()

                return {
                    "name": gym.name,
                    "invite_code": gym.invite_code,
                }

        raise RuntimeError("Could not generate a unique invite code")


def get_gym_by_id(gym_id):
    with SessionLocal() as session:
        return session.query(Gym).filter_by(id=gym_id).first()


def get_gym_by_invite_code(invite_code):
    with SessionLocal() as session:
        return session.query(Gym).filter_by(invite_code=invite_code).first()


def get_gym_members(gym_name):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=gym_name).first()

        if gym is None:
            return None

        users = (
            session.query(User)
            .filter_by(gym_id=gym.id)
            .order_by(User.username)
            .all()
        )

        return [
            {
                "username": user.username,
                "role": normalize_role(user.role),
                "is_active": user.is_active,
            }
            for user in users
        ]


def create_activity_log(
    session,
    gym_id,
    actor_username,
    action,
    target_username,
    details,
):
    session.add(
        ActivityLog(
            gym_id=gym_id,
            actor_username=actor_username,
            action=action,
            target_username=target_username,
            details=details,
        )
    )


def get_activity_log(gym_name):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=gym_name).first()

        if gym is None:
            return None

        logs = (
            session.query(ActivityLog)
            .filter_by(gym_id=gym.id)
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .all()
        )

        return [
            {
                "id": log.id,
                "gym_id": log.gym_id,
                "actor_username": log.actor_username,
                "action": log.action,
                "target_username": log.target_username,
                "details": log.details,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]


def set_user_active_state(username, gym_id, actor_username, is_active):
    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise UserActivationError("User not found", 404)

        if user.gym_id != gym_id:
            raise UserActivationError("Access denied for this gym", 403)

        user.is_active = is_active
        create_activity_log(
            session=session,
            gym_id=gym_id,
            actor_username=actor_username,
            action="user_activated" if is_active else "user_deactivated",
            target_username=user.username,
            details="User reactivated" if is_active else "User deactivated",
        )
        session.commit()

        return {
            "message": "User activated successfully" if is_active else "User deactivated successfully",
            "username": user.username,
            "is_active": user.is_active,
        }


def deactivate_user(username, gym_id, actor_username):
    return set_user_active_state(username, gym_id, actor_username, False)


def activate_user(username, gym_id, actor_username):
    return set_user_active_state(username, gym_id, actor_username, True)


def remove_member(username, requesting_user):
    if requesting_user.get("role") != "manager":
        raise RemoveMemberError("Manager access required", 403)

    if username == requesting_user.get("username"):
        raise RemoveMemberError("You cannot remove yourself", 400)

    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise RemoveMemberError("User not found", 404)

        if user.gym_id != requesting_user.get("gym_id"):
            raise RemoveMemberError("Access denied for this gym", 403)

        session.delete(user)
        session.commit()

        return {
            "username": username,
            "message": "Member removed",
        }


def update_user_role(username, new_role, manager_gym_id, actor_username):
    if new_role not in ROLE_UPDATE_TARGETS:
        raise UpdateMemberRoleError("Invalid role", 400)

    with SessionLocal() as session:
        user = session.query(User).filter_by(username=username).first()

        if user is None:
            raise UpdateMemberRoleError("User not found", 404)

        if user.gym_id != manager_gym_id:
            raise UpdateMemberRoleError("Access denied for this gym", 403)

        if normalize_role(user.role) not in ROLE_UPDATE_TARGETS:
            raise UpdateMemberRoleError("Only member and trainer roles can be changed", 400)

        old_role = normalize_role(user.role)
        user.role = new_role
        create_activity_log(
            session=session,
            gym_id=manager_gym_id,
            actor_username=actor_username,
            action="role_changed",
            target_username=user.username,
            details=f"Role changed from {old_role} to {new_role}",
        )
        session.commit()

        return {
            "message": "Role updated successfully",
            "username": user.username,
            "role": user.role,
        }


def get_capacity_alert(name: str):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        status = gym.status()
        utilization_percent = (
            round((gym.current_count / gym.max_capacity) * 100, 2)
            if gym.max_capacity
            else 0
        )
        alerts = {
            "NORMAL": {
                "severity": "info",
                "message": "Gym occupancy is normal",
            },
            "BUSY": {
                "severity": "warning",
                "message": "Gym is getting busy",
            },
            "FULL": {
                "severity": "critical",
                "message": "Gym is full",
            },
        }

        return {
            "status": status,
            "severity": alerts[status]["severity"],
            "message": alerts[status]["message"],
            "utilization_percent": utilization_percent,
        }


def get_occupancy_history(name: str):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        events = (
            session.query(OccupancyEvent)
            .filter_by(gym_id=gym.id)
            .order_by(OccupancyEvent.timestamp)
            .all()
        )

        return [
            {
                "current_count": event.current_count,
                "timestamp": event.timestamp.isoformat(),
            }
            for event in events
        ]


def get_occupancy_analytics(name: str):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=name).first()

        if gym is None:
            return None

        events = (
            session.query(OccupancyEvent)
            .filter_by(gym_id=gym.id)
            .order_by(OccupancyEvent.timestamp)
            .all()
        )
        counts = [event.current_count for event in events]
        event_count = len(counts)

        return {
            "gym": gym.name,
            "event_count": event_count,
            "average_occupancy": round(sum(counts) / event_count, 2)
            if event_count
            else 0,
            "peak_occupancy": max(counts) if event_count else 0,
            "last_count": counts[-1] if event_count else gym.current_count,
        }


def get_hourly_occupancy_averages(gym_name):
    with SessionLocal() as session:
        gym = session.query(Gym).filter_by(name=gym_name).first()

        if gym is None:
            return None

        events = (
            session.query(OccupancyEvent)
            .filter_by(gym_id=gym.id)
            .order_by(OccupancyEvent.timestamp)
            .all()
        )

        if len(events) < 2:
            return {"message": "Not enough data"}

        occupancy_by_hour = {}

        for event in events:
            hour = event.timestamp.hour
            occupancy_by_hour.setdefault(hour, []).append(event.current_count)

        return {
            hour: round(sum(counts) / len(counts))
            for hour, counts in occupancy_by_hour.items()
        }


def get_best_training_time(gym_name):
    hourly_averages = get_hourly_occupancy_averages(gym_name)

    if hourly_averages is None or "message" in hourly_averages:
        return hourly_averages

    best_hour, average_occupancy = min(
        hourly_averages.items(),
        key=lambda item: item[1],
    )

    return {
        "best_hour": best_hour,
        "label": f"{best_hour:02d}:00 - {(best_hour + 1) % 24:02d}:00",
        "average_occupancy": average_occupancy,
    }


def get_peak_hour(gym_name):
    hourly_averages = get_hourly_occupancy_averages(gym_name)

    if hourly_averages is None or "message" in hourly_averages:
        return hourly_averages

    peak_hour, average_occupancy = max(
        hourly_averages.items(),
        key=lambda item: item[1],
    )

    return {
        "peak_hour": peak_hour,
        "label": f"{peak_hour:02d}:00 - {(peak_hour + 1) % 24:02d}:00",
        "average_occupancy": average_occupancy,
    }
