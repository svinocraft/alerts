from datetime import datetime, timezone

from collections.abc import Sequence

from sqlalchemy import select, delete, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Territory,
    WhitelistEntry,
    WhitelistConfig,
    PlayerSession,
    PlayerSessionHistory,
    TrackedPlayer,
    PlayerLink,
    ProximityState,
    ChatConfig,
    AlertConfig,
    MonitorConfig,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Territory ──────────────────────────────────────────────────────────


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


async def get_territory_by_id(session: AsyncSession, territory_id: int) -> Territory | None:
    result = await session.execute(
        select(Territory).where(Territory.id == territory_id)
    )
    return result.scalar_one_or_none()


async def update_territory(
    session: AsyncSession, territory_id: int, **kwargs
) -> Territory | None:
    t = await get_territory_by_id(session, territory_id)
    if not t:
        return None
    for key, value in kwargs.items():
        if hasattr(t, key):
            setattr(t, key, value)
    await session.commit()
    return t


async def delete_territory(session: AsyncSession, territory_id: int) -> None:
    await session.execute(
        delete(ProximityState).where(ProximityState.territory_id == territory_id)
    )
    await session.execute(
        delete(PlayerSession).where(PlayerSession.territory_id == territory_id)
    )
    await session.execute(
        delete(PlayerSessionHistory).where(PlayerSessionHistory.territory_id == territory_id)
    )
    await session.execute(
        delete(WhitelistEntry).where(WhitelistEntry.territory_id == territory_id)
    )
    await session.execute(
        delete(WhitelistConfig).where(WhitelistConfig.territory_id == territory_id)
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


# ── Whitelist Entry ────────────────────────────────────────────────────


async def add_whitelist(
    session: AsyncSession, chat_id: int, player_name: str,
    territory_id: int | None = None,
) -> WhitelistEntry:
    entry = WhitelistEntry(
        chat_id=chat_id,
        territory_id=territory_id,
        player_name=player_name.lower(),
    )
    session.add(entry)
    await session.commit()
    return entry


async def remove_whitelist(
    session: AsyncSession, chat_id: int, player_name: str,
    territory_id: int | None = None,
) -> None:
    stmt = delete(WhitelistEntry).where(
        WhitelistEntry.chat_id == chat_id,
        WhitelistEntry.player_name == player_name.lower(),
        WhitelistEntry.territory_id == territory_id,
    )
    await session.execute(stmt)
    await session.commit()


async def get_whitelist(
    session: AsyncSession, chat_id: int,
    territory_id: int | None = None,
):
    stmt = select(WhitelistEntry).where(
        WhitelistEntry.chat_id == chat_id,
        WhitelistEntry.territory_id == territory_id,
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_whitelisted_players(
    session: AsyncSession, chat_id: int, territory_id: int | None = None,
) -> set[str]:
    stmt = select(WhitelistEntry.player_name).where(
        WhitelistEntry.chat_id == chat_id,
        WhitelistEntry.territory_id == territory_id,
    )
    result = await session.execute(stmt)
    return {row[0] for row in result}


async def is_whitelisted(
    session: AsyncSession, chat_id: int, player_name: str,
    territory_id: int | None = None,
) -> bool:
    result = await session.execute(
        select(WhitelistEntry).where(
            WhitelistEntry.chat_id == chat_id,
            WhitelistEntry.player_name == player_name.lower(),
            WhitelistEntry.territory_id == territory_id,
        )
    )
    return result.scalar_one_or_none() is not None


# ── Whitelist Config (on/off) ─────────────────────────────────────────


async def get_whitelist_config(
    session: AsyncSession, chat_id: int,
    territory_id: int | None = None,
) -> bool:
    result = await session.execute(
        select(WhitelistConfig).where(
            WhitelistConfig.chat_id == chat_id,
            WhitelistConfig.territory_id == territory_id,
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        return True
    return config.enabled


async def set_whitelist_config(
    session: AsyncSession, chat_id: int, enabled: bool,
    territory_id: int | None = None,
) -> None:
    result = await session.execute(
        select(WhitelistConfig).where(
            WhitelistConfig.chat_id == chat_id,
            WhitelistConfig.territory_id == territory_id,
        )
    )
    config = result.scalar_one_or_none()
    if config:
        config.enabled = enabled
    else:
        config = WhitelistConfig(
            chat_id=chat_id, territory_id=territory_id, enabled=enabled,
        )
        session.add(config)
    await session.commit()


# ── Player Session (active) ────────────────────────────────────────────


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


# ── Player Session History (for stats) ─────────────────────────────────


async def create_session_history(
    session: AsyncSession, chat_id: int, player_name: str,
    territory_id: int, entered_at: datetime, exited_at: datetime | None = None,
) -> PlayerSessionHistory:
    duration = None
    if exited_at:
        duration = int((exited_at - entered_at).total_seconds())
    psh = PlayerSessionHistory(
        chat_id=chat_id,
        player_name=player_name,
        territory_id=territory_id,
        entered_at=entered_at,
        exited_at=exited_at,
        duration_seconds=duration,
    )
    session.add(psh)
    await session.commit()
    return psh


async def finish_session_history(
    session: AsyncSession, chat_id: int, player_name: str, territory_id: int,
) -> None:
    result = await session.execute(
        select(PlayerSessionHistory).where(
            PlayerSessionHistory.chat_id == chat_id,
            PlayerSessionHistory.player_name == player_name,
            PlayerSessionHistory.territory_id == territory_id,
            PlayerSessionHistory.exited_at.is_(None),
        ).order_by(PlayerSessionHistory.entered_at.desc()).limit(1)
    )
    psh = result.scalar_one_or_none()
    if psh:
        now = _now()
        entered = psh.entered_at
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=timezone.utc)
        psh.exited_at = now
        psh.duration_seconds = int((now - entered).total_seconds())
        await session.commit()


async def get_session_history(
    session: AsyncSession, chat_id: int,
    player_name: str | None = None,
    limit: int = 50,
) -> Sequence[PlayerSessionHistory]:
    stmt = select(PlayerSessionHistory).where(
        PlayerSessionHistory.chat_id == chat_id,
    )
    if player_name:
        stmt = stmt.where(PlayerSessionHistory.player_name == player_name.lower())
    stmt = stmt.order_by(PlayerSessionHistory.entered_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_player_time_on_territories(
    session: AsyncSession, chat_id: int,
) -> list[dict]:
    result = await session.execute(
        select(PlayerSessionHistory).where(
            PlayerSessionHistory.chat_id == chat_id,
            PlayerSessionHistory.duration_seconds.is_not(None),
        )
    )
    rows = result.scalars().all()
    agg: dict[tuple[str, int], int] = {}
    for r in rows:
        key = (r.player_name, r.territory_id)
        agg[key] = agg.get(key, 0) + (r.duration_seconds or 0)
    return [
        {"player_name": pn, "territory_id": tid, "total_seconds": secs}
        for (pn, tid), secs in agg.items()
    ]


# ── Tracked Player ─────────────────────────────────────────────────────


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


# ── Player Link ────────────────────────────────────────────────────────


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


# ── Proximity State ────────────────────────────────────────────────────


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


# ── Chat Config ────────────────────────────────────────────────────────


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


# ── Alert Config ───────────────────────────────────────────────────────


async def get_alert_config(session: AsyncSession, chat_id: int) -> AlertConfig:
    result = await session.execute(
        select(AlertConfig).where(AlertConfig.chat_id == chat_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        config = AlertConfig(chat_id=chat_id)
        session.add(config)
        await session.commit()
    return config


async def update_alert_template(
    session: AsyncSession, chat_id: int, field: str, value: str
) -> AlertConfig:
    config = await get_alert_config(session, chat_id)
    if hasattr(config, field):
        setattr(config, field, value)
        await session.commit()
    return config


async def set_alert_enabled(
    session: AsyncSession, chat_id: int, field: str, enabled: bool
) -> AlertConfig:
    config = await get_alert_config(session, chat_id)
    if hasattr(config, field):
        setattr(config, field, enabled)
        await session.commit()
    return config


# ── Monitor Config ─────────────────────────────────────────────────────


async def add_monitor(
    session: AsyncSession, chat_id: int, player_name: str
) -> MonitorConfig:
    existing = await get_monitor(session, chat_id, player_name)
    if existing:
        return existing
    mc = MonitorConfig(chat_id=chat_id, player_name=player_name.lower())
    session.add(mc)
    await session.commit()
    return mc


async def remove_monitor(
    session: AsyncSession, chat_id: int, player_name: str
) -> None:
    await session.execute(
        delete(MonitorConfig).where(
            MonitorConfig.chat_id == chat_id,
            MonitorConfig.player_name == player_name.lower(),
        )
    )
    await session.commit()


async def get_monitor(
    session: AsyncSession, chat_id: int, player_name: str
) -> MonitorConfig | None:
    result = await session.execute(
        select(MonitorConfig).where(
            MonitorConfig.chat_id == chat_id,
            MonitorConfig.player_name == player_name.lower(),
        )
    )
    return result.scalar_one_or_none()


async def get_all_monitors(session: AsyncSession) -> Sequence[MonitorConfig]:
    result = await session.execute(select(MonitorConfig))
    return result.scalars().all()


async def get_chat_monitors(
    session: AsyncSession, chat_id: int
) -> Sequence[MonitorConfig]:
    result = await session.execute(
        select(MonitorConfig).where(MonitorConfig.chat_id == chat_id)
    )
    return result.scalars().all()


# ── Search ─────────────────────────────────────────────────────────────


async def search_player_across_chats(
    session: AsyncSession, player_name: str
) -> list[dict]:
    player_lower = player_name.lower()
    results = []

    # Find in territories
    t_result = await session.execute(
        select(Territory).join(
            PlayerSessionHistory,
            PlayerSessionHistory.territory_id == Territory.id,
        ).where(PlayerSessionHistory.player_name == player_lower)
    )
    territories = t_result.scalars().all()
    seen_chats = set()
    for t in territories:
        seen_chats.add(t.chat_id)

    # Find in active sessions
    s_result = await session.execute(
        select(Territory).join(
            PlayerSession,
            PlayerSession.territory_id == Territory.id,
        ).where(PlayerSession.player_name == player_lower)
    )
    for t in s_result.scalars().all():
        seen_chats.add(t.chat_id)

    # Find in tracked players
    tr_result = await session.execute(
        select(TrackedPlayer).where(TrackedPlayer.player_name == player_lower)
    )
    for tp in tr_result.scalars().all():
        seen_chats.add(tp.chat_id)

    for chat_id in seen_chats:
        chat_territories = await get_territories(session, chat_id)
        results.append({
            "chat_id": chat_id,
            "territories": [
                {"name": t.name, "world": t.world}
                for t in chat_territories
            ],
            "player_name": player_name,
        })

    return results


async def search_player_in_chat(
    session: AsyncSession, chat_id: int, player_name: str
) -> dict:
    player_lower = player_name.lower()
    territories = await get_territories(session, chat_id)

    # History in this chat
    history_result = await session.execute(
        select(PlayerSessionHistory).where(
            PlayerSessionHistory.chat_id == chat_id,
            PlayerSessionHistory.player_name == player_lower,
        ).order_by(PlayerSessionHistory.entered_at.desc())
    )
    history = history_result.scalars().all()

    # Active sessions in this chat
    active_result = await session.execute(
        select(PlayerSession).where(
            PlayerSession.chat_id == chat_id,
            PlayerSession.player_name == player_lower,
        )
    )
    active = active_result.scalars().all()

    # Tracked player
    tracked_result = await session.execute(
        select(TrackedPlayer).where(
            TrackedPlayer.chat_id == chat_id,
            TrackedPlayer.player_name == player_lower,
        )
    )
    tracked = tracked_result.scalar_one_or_none()

    return {
        "chat_id": chat_id,
        "player_name": player_name,
        "territories": [{"id": t.id, "name": t.name} for t in territories],
        "history": history,
        "active_sessions": active,
        "tracked": tracked,
    }
