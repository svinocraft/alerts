import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Kyiv"

# Mapping of common GMT offsets to IANA zones for DST-aware defaults
_GMT_TO_IANA: dict[str, str] = {
    "0": "UTC",
    "+1": "Europe/Berlin",
    "+2": "Europe/Kyiv",
    "+3": "Europe/Moscow",
    "-5": "America/New_York",
    "-8": "America/Los_Angeles",
}


def parse_timezone(tz_str: str) -> timezone | ZoneInfo:
    s = tz_str.strip()
    try:
        return ZoneInfo(s)
    except (KeyError, OSError):
        pass
    m = re.match(
        r"^(?:GMT|UTC)?([+-]\d{1,2})(?::(\d{2}))?$",
        s, re.IGNORECASE,
    )
    if m:
        hours = int(m.group(1))
        minutes = int(m.group(2)) if m.group(2) else 0
        return timezone(timedelta(hours=hours, minutes=minutes))
    raise ValueError(f"Unknown timezone: {tz_str}")


def format_dt(dt: datetime, tz_name: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    tz = parse_timezone(tz_name)
    return dt.astimezone(tz).strftime(fmt)


def normalize_tz_input(text: str) -> str:
    normalized = text.strip().lower().replace(" ", "")
    m = re.match(r"^([+-]\d{1,2})$", normalized)
    if m:
        offset = m.group(1)
        iana = _GMT_TO_IANA.get(offset)
        if iana:
            return iana
        return f"Etc/GMT{offset}"
    m = re.match(r"^(?:gmt|utc)([+-]\d{1,2}(?::\d{2})?)?$", normalized)
    if m:
        offset_part = m.group(1)
        if offset_part is None:
            return "UTC"
        hours = int(offset_part.split(":")[0])
        iana = _GMT_TO_IANA.get(f"{hours:+d}" if hours >= 0 else str(hours))
        if iana:
            return iana
        return f"Etc/GMT{offset_part.split(':')[0]}"
    return text.strip()
