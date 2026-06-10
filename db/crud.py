from datetime import datetime, timezone

from collections.abc import Sequence

from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Territory,
    WhitelistEntry,
    PlayerSession,
    TrackedPlayer,
    PlayerLink,
    ProximityState,
    ChatConfig,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_territory(
    session: AsyncSession,
    chat_id: int,
    name: str,
    shape_type: str,
    coordinates: str,
    world: str = "minecraft_overworld",
    region_id: str | None = None,
    auto_update: bool = False,
    proximity_radius: int = 50,
) -> Territory:
    t = Territory(
        chat_id=chat_id,
        name=name,
        shape_type=shape_type,
        coordinates=coordinates,
        world=world,
        region_id=region_id,
        auto_update=auto_update,
        proximity_radius=proximity_radius,
    )
    session.add(t)
    await session.commit()
    return t


async def get_territories(session: AsyncSession, chat_id: int | None = None):
    stmt = select(Territory)
    if chat_id is not None:
        stmt = stmt.where(Territory.chat_id == chat_id)
    result = await session.execute(stmt.order_by(Territory.created_at))
    return result.scalars().all()


async def get_territory_by_name(
    session: AsyncSession, chat_id: int, name: str
) -> Territory | None:
    result = await session.execute(
        select(Territory).where(
            Territory.chat_id == chat_id, Territory.name == name
        )
    )
    return result.scalar_one_or_none()


async def delete_territory(session: AsyncSession, territory_id: int) -> None:
    await session.execute(
        delete(ProximityState).where(ProximityState.territory_id == territory_id)
    )
    await session.execute(
        delete(PlayerSession).where(PlayerSession.territory_id == territory_id)
    )
    await session.execute(
        delete(Territory).where(Territory.id == territory_id)
    )
    await session.commit()


async def get_auto_update_territories(session: AsyncSession):
    result = await session.execute(
        select(Territory).where(Territory.auto_update == True)
    )
    return result.scalars().all()


async def update_territory_coords(
    session: AsyncSession, territory_id: int, coordinates: str
) -> None:
    await session.execute(
        update(Territory)
        .where(Territory.id == territory_id)
        .values(coordinates=coordinates, last_updated=_now())
    )
    await session.commit()


async def update_territory_radius(
    session: AsyncSession, territory_id: int, radius: int
) -> None:
    await session.execute(
        update(Territory)
        .where(Territory.id == territory_id)
        .values(proximity_radius=radius)
    )
    await session.commit()


async def add_whitelist(
    session: AsyncSession, chat_id: int, player_name: str
) -> WhitelistEntry:
    entry = WhitelistEntry(chat_id=chat_id, player_name=player_name.lower())
    session.add(entry)
    await session.commit()
    return entry


async def remove_whitelist(
    session: AsyncSession, chat_id: int, player_name: str
) -> None:
    await session.execute(
        delete(WhitelistEntry).where(
            WhitelistEntry.chat_id == chat_id,
            WhitelistEntry.player_name == player_name.lower(),
        )
    )
    await session.commit()


async def get_whitelist(session: AsyncSession, chat_id: int):
    result = await session.execute(
        select(WhitelistEntry).where(WhitelistEntry.chat_id == chat_id)
    )
    return result.scalars().all()


async def get_active_sessions(session: AsyncSession, chat_id: int | None = None):
    stmt = select(PlayerSession)
    if chat_id is not None:
        stmt = stmt.where(PlayerSession.chat_id == chat_id)
    result = await session.execute(stmt)
    return result.scalars().all()


async def create_session(
    session: AsyncSession, chat_id: int, player_name: str, territory_id: int
) -> PlayerSession:
    ps = PlayerSession(
        chat_id=chat_id,
        player_name=player_name,
        territory_id=territory_id,
    )
    session.add(ps)
    await session.commit()
    return ps


async def delete_session(
    session: AsyncSession, chat_id: int, player_name: str, territory_id: int
) -> None:
    await session.execute(
        delete(PlayerSession).where(
            PlayerSession.chat_id == chat_id,
            PlayerSession.player_name == player_name,
            PlayerSession.territory_id == territory_id,
        )
    )
    await session.commit()


async def upsert_tracked_player(
    session: AsyncSession, chat_id: int, player_name: str
) -> TrackedPlayer:
    now = _now()
    existing = (
        await session.execute(
            select(TrackedPlayer).where(
                TrackedPlayer.chat_id == chat_id,
                TrackedPlayer.player_name == player_name,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.last_seen = now
        tp = existing
    else:
        tp = TrackedPlayer(
            chat_id=chat_id,
            player_name=player_name,
            first_seen=now,
            last_seen=now,
        )
        session.add(tp)
    await session.commit()
    return tp


async def get_tracked_players(session: AsyncSession, chat_id: int):
    result = await session.execute(
        select(TrackedPlayer)
        .where(TrackedPlayer.chat_id == chat_id)
        .order_by(TrackedPlayer.last_seen.desc())
    )
    return result.scalars().all()


async def create_player_link(
    session: AsyncSession,
    minecraft_name: str,
    telegram_id: int | None = None,
    telegram_username: str | None = None,
    custom_name: str = "",
) -> PlayerLink | None:
    existing = await get_player_link(session, minecraft_name)
    if existing:
        existing.telegram_id = telegram_id
        existing.telegram_username = telegram_username
        existing.custom_name = custom_name or minecraft_name
        await session.commit()
        return existing
    pl = PlayerLink(
        minecraft_name=minecraft_name.lower(),
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        custom_name=custom_name or minecraft_name,
    )
    session.add(pl)
    await session.commit()
    return pl


async def delete_player_link(session: AsyncSession, minecraft_name: str) -> None:
    await session.execute(
        delete(PlayerLink).where(PlayerLink.minecraft_name == minecraft_name.lower())
    )
    await session.commit()


async def get_player_link(
    session: AsyncSession, minecraft_name: str
) -> PlayerLink | None:
    result = await session.execute(
        select(PlayerLink).where(PlayerLink.minecraft_name == minecraft_name.lower())
    )
    return result.scalar_one_or_none()


async def get_all_player_links(session: AsyncSession) -> Sequence[PlayerLink]:
    result = await session.execute(select(PlayerLink))
    return result.scalars().all()


async def set_proximity_state(
    session: AsyncSession, chat_id: int, player_name: str, territory_id: int
) -> ProximityState:
    existing = await get_proximity_state(session, chat_id, player_name, territory_id)
    if existing:
        return existing
    ps = ProximityState(
        chat_id=chat_id,
        player_name=player_name,
        territory_id=territory_id,
    )
    session.add(ps)
    await session.commit()
    return ps


async def clear_proximity_state(
    session: AsyncSession, chat_id: int, player_name: str, territory_id: int
) -> None:
    await session.execute(
        delete(ProximityState).where(
            ProximityState.chat_id == chat_id,
            ProximityState.player_name == player_name,
            ProximityState.territory_id == territory_id,
        )
    )
    await session.commit()


async def get_proximity_state(
    session: AsyncSession, chat_id: int, player_name: str, territory_id: int
) -> ProximityState | None:
    result = await session.execute(
        select(ProximityState).where(
            ProximityState.chat_id == chat_id,
            ProximityState.player_name == player_name,
            ProximityState.territory_id == territory_id,
        )
    )
    return result.scalar_one_or_none()


async def get_chat_config(
    session: AsyncSession, chat_id: int
) -> ChatConfig:
    result = await session.execute(
        select(ChatConfig).where(ChatConfig.chat_id == chat_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        config = ChatConfig(chat_id=chat_id, timezone="Europe/Kyiv")
        session.add(config)
        await session.commit()
    return config


async def update_chat_timezone(
    session: AsyncSession, chat_id: int, timezone: str
) -> ChatConfig:
    config = await get_chat_config(session, chat_id)
    config.timezone = timezone
    await session.commit()
    return config


async def get_proximity_states_for_territory(
    session: AsyncSession, chat_id: int, territory_id: int
) -> Sequence[ProximityState]:
    result = await session.execute(
        select(ProximityState).where(
            ProximityState.chat_id == chat_id,
            ProximityState.territory_id == territory_id,
        )
    )
    return result.scalars().all()
