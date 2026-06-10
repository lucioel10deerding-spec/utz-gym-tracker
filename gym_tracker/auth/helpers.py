import jwt
from flask import jsonify, request

from gym_tracker import config


LEGACY_ROLE_MAP = {
    "admin": "manager",
    "owner": "manager",
}


def normalize_role(role):
    return LEGACY_ROLE_MAP.get(role, role)


def encode_token(payload):
    normalized_payload = {
        **payload,
        "role": normalize_role(payload.get("role")),
    }

    return jwt.encode(
        normalized_payload,
        config.JWT_SECRET_KEY,
        algorithm="HS256",
    )


def decode_token(token):
    payload = jwt.decode(
        token,
        config.JWT_SECRET_KEY,
        algorithms=["HS256"],
    )

    if "role" in payload:
        payload["role"] = normalize_role(payload["role"])

    return payload


def get_current_user():
    authorization_header = request.headers.get("Authorization", "")

    if not authorization_header.startswith("Bearer "):
        return None, (jsonify({"error": "Missing token"}), 401)

    token = authorization_header.removeprefix("Bearer ").strip()

    if not token:
        return None, (jsonify({"error": "Missing token"}), 401)

    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None, (jsonify({"error": "Invalid token"}), 401)

    return payload, None
