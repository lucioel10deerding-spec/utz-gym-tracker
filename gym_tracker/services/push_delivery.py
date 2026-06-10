import logging
from datetime import datetime, timezone

from gym_tracker.adapters.database import SessionLocal
from gym_tracker.domain.models import DeviceToken, PendingNotification
from gym_tracker.services.push_providers import (
    InvalidFirebaseTokenError,
    create_push_provider,
)


logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


class PushDeliveryService:
    def __init__(
        self,
        session_factory=SessionLocal,
        delivery_logger=logger,
        push_provider=None,
    ):
        self.session_factory = session_factory
        self.logger = delivery_logger
        self.push_provider = push_provider or create_push_provider()

    def get_pending_notifications(self, session):
        return (
            session.query(PendingNotification)
            .filter_by(status="pending")
            .order_by(PendingNotification.created_at)
            .all()
        )

    def get_device_tokens_for_notification(self, session, notification):
        return (
            session.query(DeviceToken)
            .filter_by(user_id=notification.user_id)
            .order_by(DeviceToken.created_at.desc(), DeviceToken.id.desc())
            .all()
        )

    def deliver(self, session, notification):
        device_tokens = self.get_device_tokens_for_notification(session, notification)

        if notification.error_message:
            raise RuntimeError(notification.error_message)

        delivered_count = 0

        for device_token in device_tokens:
            try:
                self.push_provider.send(device_token, notification.message)
                delivered_count += 1
            except InvalidFirebaseTokenError as error:
                self.logger.warning(
                    "Removing invalid Firebase token notification_id=%s user_id=%s "
                    "device_token_id=%s error=%s",
                    notification.id,
                    notification.user_id,
                    device_token.id,
                    error,
                )
                session.delete(device_token)

        self.logger.info(
            "Push delivery notification_id=%s user_id=%s device_tokens=%s delivered=%s",
            notification.id,
            notification.user_id,
            len(device_tokens),
            delivered_count,
        )
        notification.status = "sent"
        notification.sent_at = utc_now()

        return device_tokens

    def process_pending_notifications(self):
        processed_count = 0
        failed_count = 0

        with self.session_factory() as session:
            notifications = self.get_pending_notifications(session)

            for notification in notifications:
                try:
                    self.deliver(session, notification)
                    processed_count += 1
                except Exception as error:
                    notification.status = "failed"
                    notification.error_message = str(error)
                    failed_count += 1

            session.commit()

        return {
            "processed_count": processed_count,
            "failed_count": failed_count,
        }
