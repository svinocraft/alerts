import json
import re
import time

import aiohttp

from config import API_URL


async def fetch_players() -> list[dict[str, str | float | int]] | None:
    url = f"{API_URL}/players.json?t={int(time.time() * 1000)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("players", [])
    except Exception:
        return None
    return None


async def fetch_worlds() -> list[dict[str, str]] | None:
    url = f"{API_URL}/settings.json?t={int(time.time() * 1000)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("worlds", [])
    except Exception:
        return None
    return None


def parse_wg_region_name(popup: str) -> str:
    match = re.search(r"<span[^>]*>(.*?)</span>", popup)
    if match:
        return match.group(1).strip()
    return popup


async def fetch_worldguard_regions(world: str) -> list[dict[str, str]] | None:
    url = f"{API_URL}/{world}/markers.json?t={int(time.time() * 1000)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for group in data:
                        if group.get("id") != "worldguard":
                            continue
                        result = []
                        for m in group.get("markers", []):
                            name = parse_wg_region_name(m.get("popup", ""))
                            mtype = m.get("type", "rectangle")
                            pts = m.get("points", [])
                            if mtype == "rectangle" and len(pts) >= 2:
                                coords = [pts[0]["x"], pts[0]["z"], pts[1]["x"], pts[1]["z"]]
                            elif mtype == "polygon" and len(pts) >= 3:
                                coords = [[p["x"], p["z"]] for p in pts]
                            else:
                                coords = None
                            if coords:
                                result.append({
                                    "name": name,
                                    "shape_type": "rectangle" if mtype == "rectangle" else "polygon",
                                    "coordinates": json.dumps(coords),
                                })
                        return result
    except Exception:
        return None
    return None
