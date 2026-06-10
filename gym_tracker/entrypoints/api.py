import logging

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, join_room

from gym_tracker import config
from gym_tracker.adapters.database import SessionLocal
from gym_tracker.auth.decorators import login_required, require_roles
from gym_tracker.auth.helpers import decode_token, encode_token, normalize_role
from gym_tracker.services.services import (
    DuplicateGymError,
    DeviceRegistrationError,
    GymFullError,
    NotificationPreferenceError,
    RemoveMemberError,
    UNSET,
    UpdateMemberRoleError,
    UserActivationError,
    activate_user,
    create_gym,
    create_user,
    deactivate_user,
    delete_own_device,
    enter_gym,
    generate_notifications_for_gym,
    generate_gym_invite_code,
    leave_gym,
    get_best_training_time,
    get_activity_log,
    get_capacity_alert,
    get_capacity,
    get_gym_by_id,
    get_gym_by_invite_code,
    get_gym_devices,
    get_gym_members,
    get_notification_preference,
    get_pending_notifications,
    get_push_dashboard_stats,
    get_user_notifications,
    get_occupancy_analytics,
    get_occupancy_history,
    get_peak_hour,
    get_users_to_notify,
    get_user_by_username,
    process_pending_notifications,
    remove_member,
    register_mobile_device,
    register_device_token,
    update_own_device,
    update_notification_preference,
    update_user_role,
    update_gym_settings,
    verify_password,
)

logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)
app = Flask(__name__, template_folder="templates")

socketio = SocketIO(app)
connected_users = {}


@socketio.on("connect")
def handle_socket_connect(auth):
    token = (auth or {}).get("token")

    if not token:
        return False

    try:
        payload = decode_token(token)
    except Exception:
        return False

    username = payload.get("username")
    gym_id = payload.get("gym_id")
    role = payload.get("role")

    if username is None or gym_id is None or role is None:
        return False

    connected_users[request.sid] = {
        "username": username,
        "gym_id": gym_id,
        "role": role,
    }
    room = f"gym_{gym_id}"
    join_room(room)
    logger.info(
        "Socket connected: username=%s gym_id=%s sid=%s room=%s",
        username,
        gym_id,
        request.sid,
        room,
    )


@socketio.on("disconnect")
def handle_socket_disconnect():
    connected_users.pop(request.sid, None)


@socketio.on("join_gym")
def handle_join_gym(data):
    connected_user = connected_users.get(request.sid)

    if connected_user is None:
        return False

    gym_id = connected_user["gym_id"]
    room = f"gym_{gym_id}"
    join_room(room)
    logger.info(
        "join_gym: sid=%s gym_id=%s room=%s payload=%s",
        request.sid,
        gym_id,
        room,
        data,
    )


def emit_capacity_update(name, capacity):
    gym_id = capacity["id"]
    room = f"gym_{gym_id}"
    payload = {
        "gym": name,
        "gym_id": gym_id,
        "current": capacity["current"],
        "max": capacity["max"],
        "status": capacity["status"],
    }
    socketio.emit("capacity_update", payload, room=room)
    logger.info(
        "capacity_update emitted: gym_name=%s gym_id=%s room=%s current_count=%s",
        name,
        gym_id,
        room,
        capacity["current"],
    )


def ensure_current_user_can_access_gym(name):
    route_gym = get_capacity(name)

    if route_gym is None:
        return jsonify({"error": "Gym not found"}), 404

    current_user_gym = get_gym_by_id(request.current_user["gym_id"])

    if current_user_gym is None or current_user_gym.id != route_gym["id"]:
        return jsonify({"error": "Access denied for this gym"}), 403

    return None


def get_current_user_gym_or_404():
    gym = get_gym_by_id(request.current_user["gym_id"])

    if gym is None:
        return None, (jsonify({"error": "Gym not found"}), 404)

    return gym, None


def perform_enter_gym(name):
    capacity = enter_gym(name)

    if capacity is None:
        return jsonify({"error": "Gym not found"}), 404

    emit_capacity_update(name, capacity)

    return jsonify(
        {
            "message": "Person entered gym",
            "name": name,
            "capacity": capacity,
            "status": capacity["status"],
        }
    ), 200


def perform_leave_gym(name):
    capacity = leave_gym(name)

    if capacity is None:
        return jsonify({"error": "Gym not found"}), 404

    emit_capacity_update(name, capacity)

    return jsonify(
        {
            "message": "Person left gym",
            "name": name,
            "current": capacity["current"],
            "max": capacity["max"],
            "is_full": capacity["current"] >= capacity["max"],
            "status": capacity["status"],
        }
    ), 200


@app.post("/gyms")
def create_gym_endpoint():
    logger.info("POST /gyms called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    data = request.get_json(silent=True) or {}

    name = data.get("name")
    max_capacity = data.get("max_capacity")
    invite_code = data.get("invite_code")
    if name is None or max_capacity is None:
        return jsonify({"error": "Missing required fields"}), 400

    capacity = create_gym(
        name=name,
        max_capacity=max_capacity,
        invite_code=invite_code,
    )

    return jsonify(
        {
            "message": "Gym created",
            "name": name,
            "capacity": capacity,
        }
    ), 201


@app.post("/users")
def create_user_endpoint():
    logger.info("POST /users called")
    data = request.get_json(silent=True) or {}

    username = data.get("username")
    password = data.get("password")
    invite_code = data.get("invite_code")
    notification_threshold = data.get(
        "notification_threshold_count",
        data.get("notification_threshold", "off"),
    )
    if username is None or password is None or invite_code is None:
        return jsonify({"error": "Missing required fields"}), 400

    gym = get_gym_by_invite_code(invite_code)
    if gym is None:
        return jsonify({"error": "Invalid invite code"}), 404

    try:
        user = create_user(
            username=username,
            password=password,
            gym_id=gym.id,
            notification_threshold=notification_threshold,
        )
    except NotificationPreferenceError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "gym_id": user.gym_id,
            "notification_threshold": user.notification_threshold_count,
            "notification_threshold_count": user.notification_threshold_count,
        }
    ), 201


@app.post("/login")
def login_endpoint():
    logger.info("POST /login called")
    data = request.get_json(silent=True) or {}

    username = data.get("username")
    password = data.get("password")
    user = get_user_by_username(username)

    if user is None or not verify_password(password, user.password):
        return jsonify({"error": "Invalid credentials"}), 401

    if not user.is_active:
        return jsonify({"error": "User is inactive"}), 403

    role = normalize_role(user.role)
    token = encode_token(
        {
            "username": user.username,
            "gym_id": user.gym_id,
            "role": role,
        }
    )

    return jsonify(
        {
            "message": "Login successful",
            "token": token,
            "username": user.username,
            "gym_id": user.gym_id,
            "role": role,
        }
    ), 200


@app.get("/login-page")
def login_page():
    return render_template("login.html")


@app.route("/me", methods=["GET"])
@login_required
def me():
    current_user = request.current_user

    return jsonify(
        {
            "username": current_user["username"],
            "gym_id": current_user["gym_id"],
            "role": current_user["role"],
        }
    )


@app.get("/me/notification-preference")
@login_required
def get_my_notification_preference_endpoint():
    logger.info("GET /me/notification-preference called")

    try:
        preference = get_notification_preference(request.current_user["username"])
    except NotificationPreferenceError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(preference), 200


@app.patch("/me/notification-preference")
@login_required
def update_my_notification_preference_endpoint():
    logger.info("PATCH /me/notification-preference called")
    data = request.get_json(silent=True) or {}

    try:
        preference = update_notification_preference(
            request.current_user["username"],
            data.get("notification_threshold_count", data.get("notification_threshold")),
        )
    except NotificationPreferenceError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(preference), 200


@app.get("/me/notifications")
@login_required
def get_my_notifications_endpoint():
    logger.info("GET /me/notifications called")
    notifications = get_user_notifications(request.current_user["username"])

    return jsonify({"notifications": notifications}), 200


@app.get("/notifications/pending")
def get_pending_notifications_endpoint():
    logger.info("GET /notifications/pending called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    return jsonify({"pending_notifications": get_pending_notifications()}), 200


@app.get("/push/dashboard")
def get_push_dashboard_endpoint():
    logger.info("GET /push/dashboard called")
    current_user, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    with SessionLocal() as session:
        stats = get_push_dashboard_stats(current_user["gym_id"], session)

    return jsonify(stats), 200


@app.post("/notifications/process")
def process_pending_notifications_endpoint():
    logger.info("POST /notifications/process called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    return jsonify(process_pending_notifications()), 200


@app.post("/me/device-token")
@login_required
def register_my_device_token_endpoint():
    logger.info("POST /me/device-token called")
    return register_current_user_device_response(legacy_response=True)


@app.post("/devices/register")
@login_required
def register_device_endpoint():
    logger.info("POST /devices/register called")
    return register_current_user_device_response()


def register_current_user_device_response(legacy_response=False):
    data = request.get_json(silent=True) or {}

    token = data.get("token", data.get("device_token"))
    platform = data.get("platform")
    device_name = data["device_name"] if "device_name" in data else UNSET
    push_enabled = data.get("push_enabled")

    if token is None:
        return jsonify({"error": "Missing required fields"}), 400
    if push_enabled is not None and not isinstance(push_enabled, bool):
        return jsonify({"error": "Invalid push_enabled"}), 400

    try:
        device = register_mobile_device(
            request.current_user["username"],
            token,
            platform,
            device_name=device_name,
            push_enabled=push_enabled,
        )
    except DeviceRegistrationError as error:
        return jsonify({"error": str(error)}), error.status_code

    message = "Device registered"

    if legacy_response:
        return jsonify({"message": message, "device": device}), 200

    return jsonify({"message": message, "device": device}), 200


@app.put("/devices/me")
@login_required
def update_my_device_endpoint():
    logger.info("PUT /devices/me called")
    data = request.get_json(silent=True) or {}

    token = data.get("token", data.get("device_token"))
    device_name = data["device_name"] if "device_name" in data else UNSET
    push_enabled = data.get("push_enabled")

    if token is None:
        return jsonify({"error": "Missing required fields"}), 400
    if push_enabled is not None and not isinstance(push_enabled, bool):
        return jsonify({"error": "Invalid push_enabled"}), 400

    try:
        device = update_own_device(
            request.current_user["username"],
            token,
            device_name=device_name,
            push_enabled=push_enabled,
        )
    except DeviceRegistrationError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify({"message": "Device updated", "device": device}), 200


@app.delete("/devices/me")
@login_required
def delete_my_device_endpoint():
    logger.info("DELETE /devices/me called")
    data = request.get_json(silent=True) or {}

    token = data.get("token", data.get("device_token"))

    if token is None:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        result = delete_own_device(request.current_user["username"], token)
    except DeviceRegistrationError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(result), 200


@app.get("/devices")
def get_devices_endpoint():
    logger.info("GET /devices called")
    current_user, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    with SessionLocal() as session:
        devices = get_gym_devices(current_user["gym_id"], session)

    return jsonify(devices), 200


@app.get("/users/<username>/gym")
def get_user_gym_endpoint(username):
    logger.info(f"GET /users/{username}/gym called")
    user = get_user_by_username(username)

    if user is None:
        return jsonify({"error": "User not found"}), 404

    gym = get_gym_by_id(user.gym_id)
    gym_data = (
        {
            "name": gym.name,
            "invite_code": gym.invite_code,
            "logo_url": gym.logo_url,
            "primary_color": gym.primary_color,
            "current": gym.current_count,
            "max": gym.max_capacity,
            "status": gym.status(),
        }
        if gym is not None
        else None
    )

    return jsonify(
        {
            "username": user.username,
            "gym_id": user.gym_id,
            "gym": gym_data,
        }
    ), 200


@app.delete("/users/<username>")
def remove_member_endpoint(username):
    logger.info(f"DELETE /users/{username} called")
    requesting_user, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    try:
        result = remove_member(username, requesting_user)
    except RemoveMemberError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(result), 200


@app.post("/users/<username>/deactivate")
def deactivate_user_endpoint(username):
    logger.info(f"POST /users/{username}/deactivate called")
    requesting_user, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    try:
        result = deactivate_user(
            username,
            requesting_user.get("gym_id"),
            requesting_user.get("username"),
        )
    except UserActivationError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(result), 200


@app.post("/users/<username>/activate")
def activate_user_endpoint(username):
    logger.info(f"POST /users/{username}/activate called")
    requesting_user, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    try:
        result = activate_user(
            username,
            requesting_user.get("gym_id"),
            requesting_user.get("username"),
        )
    except UserActivationError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(result), 200


@app.put("/users/<username>/role")
def update_member_role_endpoint(username):
    logger.info(f"PUT /users/{username}/role called")
    requesting_user, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    data = request.get_json(silent=True) or {}

    try:
        result = update_user_role(
            username,
            data.get("role"),
            requesting_user.get("gym_id"),
            requesting_user.get("username"),
        )
    except UpdateMemberRoleError as error:
        return jsonify({"error": str(error)}), error.status_code

    return jsonify(result), 200


@app.get("/my-gym")
@login_required
def get_my_gym_endpoint():
    logger.info("GET /my-gym called")
    current_user = request.current_user
    username = current_user.get("username")
    gym_id = current_user.get("gym_id")
    gym = get_gym_by_id(gym_id)
    gym_data = (
        {
            "name": gym.name,
            "invite_code": gym.invite_code,
            "logo_url": gym.logo_url,
            "primary_color": gym.primary_color,
            "current": gym.current_count,
            "max": gym.max_capacity,
            "status": gym.status(),
        }
        if gym is not None
        else None
    )

    return jsonify(
        {
            "username": username,
            "gym": gym_data,
        }
    ), 200


@app.get("/gyms/<name>")
def get_gym_endpoint(name):
    logger.info(f"GET /gyms/{name} called")
    capacity = get_capacity(name)

    if capacity is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(
        {
            "name": name,
            "current": capacity["current"],
            "max": capacity["max"],
            "is_full": capacity["current"] >= capacity["max"],
            "status": capacity["status"],
        }
    ), 200


@app.get("/gyms/<name>/history")
def get_gym_history_endpoint(name):
    logger.info(f"GET /gyms/{name}/history called")
    history = get_occupancy_history(name)

    if history is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(history)


@app.get("/gyms/<name>/analytics")
def get_gym_analytics_endpoint(name):
    logger.info(f"GET /gyms/{name}/analytics called")
    analytics = get_occupancy_analytics(name)

    if analytics is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(analytics)


@app.get("/gyms/<name>/best-time")
def get_gym_best_time_endpoint(name):
    logger.info(f"GET /gyms/{name}/best-time called")
    best_time = get_best_training_time(name)

    if best_time is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(best_time), 200


@app.get("/gyms/<name>/peak-hour")
def get_gym_peak_hour_endpoint(name):
    logger.info(f"GET /gyms/{name}/peak-hour called")
    peak_hour = get_peak_hour(name)

    if peak_hour is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(peak_hour), 200


@app.get("/gyms/<name>/alerts")
def get_gym_alerts_endpoint(name):
    logger.info(f"GET /gyms/{name}/alerts called")
    alert = get_capacity_alert(name)

    if alert is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(alert)


@app.get("/gyms/<name>/notifications/eligible-users")
def get_notification_eligible_users_endpoint(name):
    logger.info(f"GET /gyms/{name}/notifications/eligible-users called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    access_error = ensure_current_user_can_access_gym(name)

    if access_error is not None:
        return access_error

    users = get_users_to_notify(name)

    if users is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify({"eligible_users": users}), 200


@app.post("/gyms/<name>/notifications/generate")
def generate_notifications_endpoint(name):
    logger.info(f"POST /gyms/{name}/notifications/generate called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    access_error = ensure_current_user_can_access_gym(name)

    if access_error is not None:
        return access_error

    notifications = generate_notifications_for_gym(name)

    if notifications is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify({"notifications": notifications, "created": len(notifications)}), 201


@app.get("/gyms/<name>/activity-log")
def get_activity_log_endpoint(name):
    logger.info(f"GET /gyms/{name}/activity-log called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    access_error = ensure_current_user_can_access_gym(name)

    if access_error is not None:
        return access_error

    activity_log = get_activity_log(name)

    if activity_log is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(activity_log), 200


@app.get("/gyms/<name>/members")
def get_gym_members_endpoint(name):
    logger.info(f"GET /gyms/{name}/members called")
    _, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    access_error = ensure_current_user_can_access_gym(name)

    if access_error is not None:
        return access_error

    members = get_gym_members(name)

    if members is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(members), 200


@app.put("/gyms/<name>/settings")
def update_gym_settings_endpoint(name):
    logger.info(f"PUT /gyms/{name}/settings called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    access_error = ensure_current_user_can_access_gym(name)

    if access_error is not None:
        return access_error

    data = request.get_json(silent=True) or {}
    settings = update_gym_settings(
        name=name,
        max_capacity=data.get("max_capacity"),
        logo_url=data.get("logo_url"),
        primary_color=data.get("primary_color"),
    )

    if settings is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(settings), 200


@app.post("/gyms/<name>/invite-code")
def generate_gym_invite_code_endpoint(name):
    logger.info(f"POST /gyms/{name}/invite-code called")
    _, auth_error = require_roles("manager")

    if auth_error is not None:
        return auth_error

    access_error = ensure_current_user_can_access_gym(name)

    if access_error is not None:
        return access_error

    try:
        invite_code = generate_gym_invite_code(name)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 500

    if invite_code is None:
        return jsonify({"error": "Gym not found"}), 404

    return jsonify(invite_code), 200


@app.post("/gyms/<name>/enter")
def enter_gym_endpoint(name):
    logger.info(f"POST /gyms/{name}/enter called")
    _, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    tenant_error = ensure_current_user_can_access_gym(name)

    if tenant_error is not None:
        return tenant_error

    return perform_enter_gym(name)


@app.post("/my-gym/enter")
@login_required
def enter_my_gym_endpoint():
    logger.info("POST /my-gym/enter called")
    _, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    gym, gym_error = get_current_user_gym_or_404()

    if gym_error is not None:
        return gym_error

    return perform_enter_gym(gym.name)


@app.route("/gyms/<name>/leave", methods=["POST"])
def leave_gym_endpoint(name):
    logger.info(f"POST /gyms/{name}/leave called")
    _, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    tenant_error = ensure_current_user_can_access_gym(name)

    if tenant_error is not None:
        return tenant_error

    return perform_leave_gym(name)


@app.post("/my-gym/leave")
@login_required
def leave_my_gym_endpoint():
    logger.info("POST /my-gym/leave called")
    _, auth_error = require_roles("trainer", "manager")

    if auth_error is not None:
        return auth_error

    gym, gym_error = get_current_user_gym_or_404()

    if gym_error is not None:
        return gym_error

    return perform_leave_gym(gym.name)


@app.get("/dashboard/<name>")
def dashboard(name):
    logger.info(f"GET /dashboard/{name} called")
    capacity = get_capacity(name)

    if capacity is None:
        return "Gym not found", 404

    current = capacity["current"]
    max_capacity = capacity["max"]
    status = capacity["status"]

    return render_template(
        "dashboard.html",
        name=name,
        current=current,
        max=max_capacity,
        status=status,
    )


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(DuplicateGymError)
def duplicate_gym(error):
    return jsonify({"error": str(error)}), 400


@app.errorhandler(GymFullError)
def gym_full(error):
    return jsonify({"error": str(error)}), 409


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    socketio.run(app, debug=True)
