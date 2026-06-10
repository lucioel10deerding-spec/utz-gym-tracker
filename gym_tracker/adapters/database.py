from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from gym_tracker import config
from gym_tracker.domain.models import Base


def build_engine(database_url: str):
    return create_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )


engine = build_engine(config.DATABASE_URL)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db():
    Base.metadata.create_all(bind=engine)
    ensure_user_is_active_column()
    ensure_user_notification_threshold_column()
    ensure_user_notification_threshold_count_column()
    ensure_device_token_mobile_columns()


def ensure_user_is_active_column():
    inspector = inspect(engine)

    if "users" not in inspector.get_table_names():
        return

    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "is_active" in user_columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
            )
        )


def ensure_user_notification_threshold_column():
    inspector = inspect(engine)

    if "users" not in inspector.get_table_names():
        return

    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "notification_threshold" in user_columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN notification_threshold VARCHAR(10) NOT NULL DEFAULT 'off'"
            )
        )


def ensure_user_notification_threshold_count_column():
    inspector = inspect(engine)

    if "users" not in inspector.get_table_names():
        return

    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "notification_threshold_count" in user_columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE users "
                "ADD COLUMN notification_threshold_count VARCHAR(10) NOT NULL DEFAULT 'off'"
            )
        )
        rows = connection.execute(
            text(
                "SELECT users.id, users.notification_threshold, gyms.max_capacity "
                "FROM users JOIN gyms ON gyms.id = users.gym_id"
            )
        )

        for user_id, threshold, max_capacity in rows:
            if threshold == "off":
                threshold_count = "off"
            else:
                threshold_count = str(
                    max(1, min(max_capacity, (max_capacity * int(threshold) + 99) // 100))
                )

            connection.execute(
                text(
                    "UPDATE users "
                    "SET notification_threshold_count = :threshold_count "
                    "WHERE id = :user_id"
                ),
                {"threshold_count": threshold_count, "user_id": user_id},
            )


def ensure_device_token_mobile_columns():
    inspector = inspect(engine)

    if "device_tokens" not in inspector.get_table_names():
        return

    device_columns = {column["name"] for column in inspector.get_columns("device_tokens")}

    with engine.begin() as connection:
        if "device_name" not in device_columns:
            connection.execute(
                text("ALTER TABLE device_tokens ADD COLUMN device_name VARCHAR")
            )

        if "last_seen" not in device_columns:
            connection.execute(
                text("ALTER TABLE device_tokens ADD COLUMN last_seen DATETIME")
            )
            connection.execute(
                text(
                    "UPDATE device_tokens "
                    "SET last_seen = COALESCE(created_at, CURRENT_TIMESTAMP) "
                    "WHERE last_seen IS NULL"
                )
            )

        if "push_enabled" not in device_columns:
            connection.execute(
                text(
                    "ALTER TABLE device_tokens "
                    "ADD COLUMN push_enabled BOOLEAN NOT NULL DEFAULT 1"
                )
            )


def configure_database(database_url: str):
    global engine

    engine = build_engine(database_url)
    SessionLocal.configure(bind=engine)
    init_db()


init_db()
