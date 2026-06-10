from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


class GymFullError(Exception):
    pass


class Gym(Base):
    __tablename__ = "gyms"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    invite_code = Column(String(100), unique=True, nullable=False)
    logo_url = Column(String, nullable=True)
    primary_color = Column(String, nullable=True)
    current_count = Column(Integer, nullable=False, default=0)
    max_capacity = Column(Integer, nullable=False)

    def enter(self):
        if self.is_full():
            raise GymFullError("Gym is full")

        self.current_count += 1

    def leave(self):
        if self.current_count > 0:
            self.current_count -= 1

    def is_full(self):
        return self.current_count >= self.max_capacity

    def status(self):
        if self.is_full():
            return "FULL"
        if self.current_count >= self.max_capacity * 0.7:
            return "BUSY"
        return "NORMAL"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    password = Column(String(200), nullable=False)
    role = Column(String(50), nullable=False, default="member")
    is_active = Column(Boolean, nullable=False, default=True)
    notification_threshold = Column(String(10), nullable=False, default="off")
    notification_threshold_count = Column(String(10), nullable=False, default="off")
    gym_id = Column(Integer, ForeignKey("gyms.id"))
    device_tokens = relationship(
        "DeviceToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class DeviceToken(Base):
    __tablename__ = "device_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "token", name="uq_device_tokens_user_token"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String, nullable=False)
    device_name = Column(String, nullable=True)
    platform = Column(String(20), nullable=False, default="unknown")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    last_seen = Column(DateTime, nullable=True, default=utc_now)
    push_enabled = Column(Boolean, nullable=False, default=True)

    user = relationship("User", back_populates="device_tokens")


class OccupancyEvent(Base):
    __tablename__ = "occupancy_events"

    id = Column(Integer, primary_key=True)
    gym_id = Column(Integer, ForeignKey("gyms.id"), nullable=False)
    current_count = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=utc_now)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.id"), nullable=False)
    message = Column(String, nullable=False)
    threshold = Column(String(10), nullable=False)
    current_utilization_percent = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    is_read = Column(Boolean, nullable=False, default=False)


class PendingNotification(Base):
    __tablename__ = "pending_notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    gym_id = Column(Integer, ForeignKey("gyms.id"), nullable=False)
    message = Column(String, nullable=False)
    threshold = Column(String(10), nullable=False)
    utilization = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    sent_at = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True)
    gym_id = Column(Integer, ForeignKey("gyms.id"), nullable=False)
    actor_username = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    target_username = Column(String(100), nullable=False)
    details = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
