import asyncio
import json
import logging
import os
from collections.abc import Sequence
from collections import defaultdict

from aiogram import Bot

from config import AUTO_UPDATE_INTERVAL, POLL_INTERVAL
from db import crud
from db.database import async_session
from db.models import PlayerLink, Territory
from services.api import fetch_players, fetch_worldguard_regions
from services.geometry import is_inside, is_near_territory

logger = logging.getLogger(__name__)

_api_errors = 0
_cached_players: list[dict[str, str | float | int]] | None = None
_name_casing: dict[str, str] = {}
_monitor_seen_state: dict[str, dict[str, bool]] = defaultdict(dict)


def _build_link_map(links: Sequence[PlayerLink]) -> dict[str, PlayerLink]:
    return {l.minecraft_name.lower(): l for l in links}


def _fmt_name(
    player_name: str,
    name_map: dict[str, str],
    link_map: dict[str, PlayerLink],
) -> str:
    original = name_map.get(player_name, player_name)
    link = link_map.get(player_name)
    if link:
        display = link.custom_name or original
        if link.telegram_id:
            return f'<a href="tg://user?id={link.telegram_id}">{display}</a>'
        if link.telegram_username:
            return f'<a href="tg://resolve?domain={link.telegram_username}">{display}</a>'
    return original


def get_name_casing() -> dict[str, str]:
    return dict(_name_casing)


async def _should_ignore(
    session, chat_id: int, player_lower: str, territory_id: int,
) -> bool:
    """Check if a player should NOT trigger alerts for this territory."""
    global_enabled = await crud.get_whitelist_config(session, chat_id, territory_id=None)
    if global_enabled:
        global_list = await crud.get_whitelisted_players(session, chat_id, territory_id=None)
        if player_lower in global_list:
            return True
    ter_enabled = await crud.get_whitelist_config(session, chat_id, territory_id=territory_id)
    if ter_enabled:
        ter_list = await crud.get_whitelisted_players(session, chat_id, territory_id=territory_id)
        if player_lower in ter_list:
            return True
    return False


async def process_players(bot: Bot) -> None:
    global _api_errors, _cached_players
    global _name_casing
    global _monitor_seen_state

    players = await fetch_players()
    if players is not None:
        _api_errors = 0
        _cached_players = players
        for p in players:
            original = str(p["name"])
            _name_casing[original.lower()] = original
    else:
        _api_errors += 1
        if _cached_players is None:
            return
        players = _cached_players
        logger.warning("Using cached player data (error streak=%d)", _api_errors)

    async with async_session() as session:
        territories = await crud.get_territories(session)
        if not territories:
            return

        links = await crud.get_all_player_links(session)
        link_map = _build_link_map(links)

        by_chat: dict[int, list[Territory]] = defaultdict(list)
        for t in territories:
            by_chat[t.chat_id].append(t)

        for chat_id, chat_territories in by_chat.items():
            current_sessions = await crud.get_active_sessions(session, chat_id)
            prev_by_territory: dict[int, set[str]] = defaultdict(set)
            for s in current_sessions:
                prev_by_territory[s.territory_id].add(s.player_name)

            world_names: dict[str, dict[str, str]] = {}
            global_names: dict[str, str] = {}
            for p in players:
                w = str(p.get("world", ""))
                if w not in world_names:
                    world_names[w] = {}
                original = str(p["name"])
                lc = original.lower()
                world_names[w][lc] = original
                global_names[lc] = original

            for territory in chat_territories:
                inside_now: set[str] = set()
                name_map = world_names.get(territory.world, {})
                for p in players:
                    if p.get("world") != territory.world:
                        continue
                    px, pz = float(p["x"]), float(p["z"])
                    if is_inside(px, pz, territory.shape_type, territory.coordinates):
                        lower = str(p["name"]).lower()
                        inside_now.add(lower)

                prev_inside = prev_by_territory.get(territory.id, set())
                entered = inside_now - prev_inside
                exited = prev_inside - inside_now

                for player_name in entered:
                    now = crud._now()
                    await crud.create_session(
                        session, chat_id, player_name, territory.id
                    )
                    await crud.create_session_history(
                        session, chat_id, player_name, territory.id,
                        entered_at=now,
                    )
                    await crud.upsert_tracked_player(session, chat_id, player_name)
                    await crud.clear_proximity_state(
                        session, chat_id, player_name, territory.id
                    )
                    if not await _should_ignore(session, chat_id, player_name, territory.id):
                        ac = await crud.get_alert_config(session, chat_id)
                        if ac.enter_enabled:
                            display = _fmt_name(player_name, name_map, link_map)
                            try:
                                msg = ac.enter_template.format(
                                    player=display,
                                    territory=territory.name,
                                    world=territory.world,
                                )
                                await bot.send_message(chat_id, msg)
                            except Exception:
                                logger.exception(
                                    "Failed to send enter notification to %s", chat_id
                                )

                for player_name in exited:
                    await crud.delete_session(
                        session, chat_id, player_name, territory.id
                    )
                    await crud.finish_session_history(
                        session, chat_id, player_name, territory.id
                    )
                    await crud.upsert_tracked_player(session, chat_id, player_name)
                    display = _fmt_name(player_name, global_names, link_map)
                    if not await _should_ignore(session, chat_id, player_name, territory.id):
                        ac = await crud.get_alert_config(session, chat_id)
                        if ac.exit_enabled:
                            try:
                                msg = ac.exit_template.format(
                                    player=display,
                                    territory=territory.name,
                                    world=territory.world,
                                )
                                await bot.send_message(chat_id, msg)
                            except Exception:
                                logger.exception(
                                    "Failed to send exit notification to %s", chat_id
                                )
                    for p in players:
                        p_lower = str(p["name"]).lower()
                        if p_lower == player_name:
                            px, pz = float(p["x"]), float(p["z"])
                            if territory.proximity_radius > 0 and not is_inside(
                                px, pz, territory.shape_type, territory.coordinates
                            ) and is_near_territory(
                                px, pz,
                                territory.shape_type,
                                territory.coordinates,
                                territory.proximity_radius,
                            ):
                                await crud.set_proximity_state(
                                    session, chat_id, player_name, territory.id
                                )
                            break

                if territory.proximity_radius <= 0:
                    continue

                prox_states = await crud.get_proximity_states_for_territory(
                    session, chat_id, territory.id
                )
                prev_near = {ps.player_name for ps in prox_states}
                near_now: set[str] = set()
                near_name_map: dict[str, str] = {}

                for p in players:
                    if p.get("world") != territory.world:
                        continue
                    px, pz = float(p["x"]), float(p["z"])
                    if is_inside(px, pz, territory.shape_type, territory.coordinates):
                        continue
                    if is_near_territory(
                        px, pz,
                        territory.shape_type,
                        territory.coordinates,
                        territory.proximity_radius,
                    ):
                        original = str(p["name"])
                        lower = original.lower()
                        near_now.add(lower)
                        near_name_map[lower] = original

                newly_near = near_now - prev_near
                for player_name in newly_near:
                    if player_name in inside_now:
                        continue
                    await crud.set_proximity_state(
                        session, chat_id, player_name, territory.id
                    )
                    if not await _should_ignore(session, chat_id, player_name, territory.id):
                        ac = await crud.get_alert_config(session, chat_id)
                        if ac.proximity_enabled:
                            display = _fmt_name(player_name, near_name_map, link_map)
                            try:
                                msg = ac.proximity_template.format(
                                    player=display,
                                    territory=territory.name,
                                    world=territory.world,
                                )
                                await bot.send_message(chat_id, msg)
                            except Exception:
                                logger.exception(
                                    "Failed to send proximity notification to %s", chat_id
                                )

                no_longer_near = prev_near - near_now
                for player_name in no_longer_near:
                    await crud.clear_proximity_state(
                        session, chat_id, player_name, territory.id
                    )

        all_monitors = await crud.get_all_monitors(session)
        monitors_by_chat: dict[int, set[str]] = defaultdict(set)
        for m in all_monitors:
            monitors_by_chat[m.chat_id].add(m.player_name)

        current_players_lower = {str(p["name"]).lower() for p in players}
        player_world_map = {str(p["name"]).lower(): str(p.get("world", "?")) for p in players}

        for chat_id, monitored_set in monitors_by_chat.items():
            prev_seen = _monitor_seen_state.get(chat_id, {})
            for player_lower in monitored_set:
                is_online = player_lower in current_players_lower
                was_online = prev_seen.get(player_lower, False)

                if is_online and not was_online:
                    ac = await crud.get_alert_config(session, chat_id)
                    if ac.monitor_join_enabled:
                        world = player_world_map.get(player_lower, "?")
                        name_map_local = {player_lower: str(next(
                            (p["name"] for p in players if str(p["name"]).lower() == player_lower),
                            player_lower,
                        ))}
                        display = _fmt_name(player_lower, name_map_local, link_map)
                        try:
                            msg = ac.monitor_join_template.format(
                                player=display,
                                world=world,
                                territory="?",
                            )
                            await bot.send_message(chat_id, msg)
                        except Exception:
                            logger.exception("Failed to send monitor join to %s", chat_id)

                elif not is_online and was_online:
                    ac = await crud.get_alert_config(session, chat_id)
                    if ac.monitor_leave_enabled:
                        name_map_offline = {player_lower: player_lower}
                        display = _fmt_name(player_lower, name_map_offline, link_map)
                        try:
                            msg = ac.monitor_leave_template.format(
                                player=display,
                                world="?",
                                territory="?",
                            )
                            await bot.send_message(chat_id, msg)
                        except Exception:
                            logger.exception("Failed to send monitor leave to %s", chat_id)

            _monitor_seen_state[chat_id] = {
                p: (p in current_players_lower)
                for p in monitored_set
            }


async def auto_update_loop() -> None:
    await asyncio.sleep(AUTO_UPDATE_INTERVAL)
    while True:
        try:
            async with async_session() as session:
                territories = await crud.get_auto_update_territories(session)
                by_world: dict[str, list[Territory]] = defaultdict(list)
                for t in territories:
                    by_world[t.world].append(t)

                for world, wg_territories in by_world.items():
                    regions = await fetch_worldguard_regions(world)
                    if not regions:
                        continue
                    region_map = {r["name"]: r for r in regions}
                    for t in wg_territories:
                        if t.region_id and t.region_id in region_map:
                            r = region_map[t.region_id]
                            await crud.update_territory_coords(
                                session, t.id, r["coordinates"]
                            )
                            logger.info(
                                "Auto-updated territory %s (%s)", t.name, world
                            )
        except Exception:
            logger.exception("Auto-update cycle error")
        await asyncio.sleep(AUTO_UPDATE_INTERVAL)


async def tracker_loop(bot: Bot) -> None:
    await asyncio.sleep(3)
    logger.info("Tracker started")
    loop_task = asyncio.create_task(auto_update_loop())
    while True:
        try:
            await process_players(bot)
        except Exception:
            logger.exception("Tracker cycle error")
        if _api_errors >= 3:
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(1)
