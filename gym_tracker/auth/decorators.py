from functools import wraps

from flask import jsonify, request

from gym_tracker.auth.helpers import get_current_user


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        payload, auth_error = get_current_user()

        if auth_error is not None:
            return auth_error

        request.current_user = payload

        return f(*args, **kwargs)

    return decorated_function


def require_roles(*roles):
    payload, auth_error = get_current_user()

    if auth_error is not None:
        return None, auth_error

    if payload.get("role") not in roles:
        if set(roles).issubset({"manager"}):
            message = "Manager access required"
        else:
            message = "Trainer or manager access required"

        return None, (jsonify({"error": message}), 403)

    request.current_user = payload

    return payload, None
