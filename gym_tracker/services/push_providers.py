import json
from abc import ABC, abstractmethod

from gym_tracker import config


class PushProviderError(Exception):
    pass


class PushProviderConfigurationError(PushProviderError):
    pass


class InvalidFirebaseTokenError(PushProviderError):
    pass


class PushProvider(ABC):
    @abstractmethod
    def send(self, device_token, message):
        pass


class FakePushProvider(PushProvider):
    def send(self, device_token, message):
        return {
            "success": True,
            "token": device_token.token,
            "platform": device_token.platform,
            "message": message,
        }


class FirebasePushProvider(PushProvider):
    def __init__(
        self,
        firebase_admin_module=None,
        credentials_module=None,
        messaging_module=None,
    ):
        self.firebase_admin = firebase_admin_module
        self.credentials = credentials_module
        self.messaging = messaging_module
        self._app = None

    def ensure_firebase_modules(self):
        if self.firebase_admin and self.credentials and self.messaging:
            return

        try:
            import firebase_admin
            from firebase_admin import credentials, messaging
        except ImportError as error:
            raise PushProviderConfigurationError(
                "Firebase Admin SDK is not installed. Install firebase-admin."
            ) from error

        self.firebase_admin = firebase_admin
        self.credentials = credentials
        self.messaging = messaging

    def build_credential(self):
        credentials_path = config.get_firebase_credentials_path()
        service_account_json = config.get_firebase_service_account_json()

        if credentials_path:
            return self.credentials.Certificate(credentials_path)

        if service_account_json:
            try:
                service_account_info = json.loads(service_account_json)
            except json.JSONDecodeError as error:
                raise PushProviderConfigurationError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON."
                ) from error

            return self.credentials.Certificate(service_account_info)

        raise PushProviderConfigurationError(
            "Firebase credentials are not configured. Set FIREBASE_CREDENTIALS_PATH "
            "or FIREBASE_SERVICE_ACCOUNT_JSON."
        )

    def get_app(self):
        self.ensure_firebase_modules()

        if self._app is not None:
            return self._app

        if getattr(self.firebase_admin, "_apps", None):
            self._app = self.firebase_admin.get_app()
            return self._app

        self._app = self.firebase_admin.initialize_app(self.build_credential())
        return self._app

    def is_invalid_token_error(self, error):
        invalid_error_names = {
            "UnregisteredError",
            "SenderIdMismatchError",
            "InvalidArgumentError",
        }

        return error.__class__.__name__ in invalid_error_names

    def send(self, device_token, message):
        self.get_app()
        firebase_message = self.messaging.Message(
            notification=self.messaging.Notification(
                title="UTZ Gym Alert",
                body=message,
            ),
            data={
                "source": "utz",
                "platform": device_token.platform,
            },
            token=device_token.token,
        )

        try:
            message_id = self.messaging.send(firebase_message)
        except Exception as error:
            if self.is_invalid_token_error(error):
                raise InvalidFirebaseTokenError(
                    f"Invalid Firebase device token: {device_token.token}"
                ) from error

            raise PushProviderError(f"Firebase push failed: {error}") from error

        return {
            "success": True,
            "provider": "firebase",
            "token": device_token.token,
            "platform": device_token.platform,
            "message": message,
            "firebase_message_id": message_id,
        }


def create_push_provider(provider_name=None):
    selected_provider = (
        provider_name if provider_name is not None else config.get_push_provider_name()
    ).strip().lower()

    if selected_provider == "fake":
        return FakePushProvider()

    if selected_provider == "firebase":
        return FirebasePushProvider()

    raise ValueError(f"Unsupported PUSH_PROVIDER: {selected_provider}")
