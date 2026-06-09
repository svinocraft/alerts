from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
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
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    added_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (UniqueConstraint("chat_id", "player_name", name="uq_whitelist"),)


class PlayerSession(Base):
    __tablename__ = "player_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(nullable=False, index=True)
    player_name: Mapped[str] = mapped_column(String(100), nullable=False)
    territory_id: Mapped[int] = mapped_column(ForeignKey("territories.id"), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(default=_now)


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
