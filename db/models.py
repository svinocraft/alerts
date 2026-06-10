from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Territory(Base):
    __tablename__ = "territories"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    shape_type: Mapped[str] = mapped_column(String(20), nullable=False)
    coordinates: Mapped[str] = mapped_column(nullable=False)
    world: Mapped[str] = mapped_column(String(100), nullable=False, default="minecraft_overworld")
    region_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    proximity_radius: Mapped[int] = mapped_column(default=50)


class WhitelistEntry(Base):
    __tablename__ = "whitelist"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    territory_id: Mapped[int | None] = mapped_column(ForeignKey("territories.id", ondelete="CASCADE"), nullable=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    added_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("chat_id", "territory_id", "player_name", name="uq_whitelist"),
    )


class WhitelistConfig(Base):
    __tablename__ = "whitelist_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    territory_id: Mapped[int | None] = mapped_column(ForeignKey("territories.id", ondelete="CASCADE"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("chat_id", "territory_id", name="uq_whitelist_config"),
    )


class PlayerSession(Base):
    __tablename__ = "player_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    territory_id: Mapped[int] = mapped_column(ForeignKey("territories.id"), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(default=_now)


class PlayerSessionHistory(Base):
    __tablename__ = "player_session_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    territory_id: Mapped[int] = mapped_column(ForeignKey("territories.id"), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(nullable=True)


class TrackedPlayer(Base):
    __tablename__ = "tracked_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(default=_now)
    last_seen: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (UniqueConstraint("chat_id", "player_name", name="uq_tracked"),)


class PlayerLink(Base):
    __tablename__ = "player_links"

    minecraft_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    custom_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ChatConfig(Base):
    __tablename__ = "chat_configs"

    chat_id: Mapped[int] = mapped_column(primary_key=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Europe/Kyiv")


class ProximityState(Base):
    __tablename__ = "proximity_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    territory_id: Mapped[int] = mapped_column(ForeignKey("territories.id"), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("chat_id", "player_name", "territory_id", name="uq_proximity"),
    )


class AlertConfig(Base):
    __tablename__ = "alert_configs"

    chat_id: Mapped[int] = mapped_column(primary_key=True)
    enter_template: Mapped[str] = mapped_column(Text, nullable=False,
        default="⚠️ {player} зайшов на територію {territory}")
    enter_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    exit_template: Mapped[str] = mapped_column(Text, nullable=False,
        default="🟠 {player} вийшов з території {territory}")
    exit_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proximity_template: Mapped[str] = mapped_column(Text, nullable=False,
        default="🟡 {player} блукає поблизу території {territory}")
    proximity_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monitor_join_template: Mapped[str] = mapped_column(Text, nullable=False,
        default="🟢 {player} зайшов на сервер ({world})")
    monitor_join_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    monitor_leave_template: Mapped[str] = mapped_column(Text, nullable=False,
        default="🔴 {player} вийшов з сервера")
    monitor_leave_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MonitorConfig(Base):
    __tablename__ = "monitor_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (UniqueConstraint("chat_id", "player_name", name="uq_monitor"),)
