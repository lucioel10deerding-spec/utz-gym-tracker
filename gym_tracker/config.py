import os

ENV = os.getenv("GYM_TRACKER_ENV", "development")
DEBUG = ENV == "development"
DATABASE_URL = os.getenv("GYM_TRACKER_DATABASE_URL", "sqlite:///gym.db")
LOG_LEVEL = os.getenv("GYM_TRACKER_LOG_LEVEL", "INFO")
JWT_SECRET_KEY = os.getenv(
    "GYM_TRACKER_JWT_SECRET_KEY",
    "utz-development-jwt-secret-key-with-at-least-32-bytes",
)


def get_push_provider_name():
    return os.getenv("PUSH_PROVIDER", "fake").strip().lower()


def get_firebase_credentials_path():
    return os.getenv("FIREBASE_CREDENTIALS_PATH")


def get_firebase_service_account_json():
    return os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")


PUSH_PROVIDER = get_push_provider_name()
