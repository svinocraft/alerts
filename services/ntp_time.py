import logging
import time
from datetime import datetime, timezone

import ntplib

logger = logging.getLogger(__name__)

NTP_SERVER = "pool.ntp.org"
RESYNC_INTERVAL = 1800

_offset: float = 0.0
_last_sync: float = 0


def _query_offset() -> float:
    try:
        client = ntplib.NTPClient()
        response = client.request(NTP_SERVER, timeout=5)
        ntp_time = response.tx_time
        sys_time = time.time()
        return ntp_time - sys_time
    except Exception:
        logger.warning("NTP query failed, using system time")
        return 0.0


def sync_ntp() -> None:
    global _offset, _last_sync
    _offset = _query_offset()
    _last_sync = time.time()
    if _offset != 0:
        logger.info("NTP offset: %.3f seconds", _offset)


def ntp_now() -> datetime:
    global _last_sync
    now = time.time()
    if now - _last_sync > RESYNC_INTERVAL:
        sync_ntp()
    return datetime.fromtimestamp(now + _offset, tz=timezone.utc)
