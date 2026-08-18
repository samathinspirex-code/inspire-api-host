import time
from collections import defaultdict

from app.core.config import settings
from app.core.errors import APIError

# In-memory sliding window, per process — fine for a single-instance deployment.
# Swap for a shared store (e.g. Redis) if the API ever runs multiple workers/instances.
_requests: dict[str, list[float]] = defaultdict(list)


def check_ip_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window_start = now - 3600
    key = f"authenticator:{ip}"
    timestamps = [t for t in _requests[key] if t > window_start]

    if len(timestamps) >= settings.AUTHENTICATOR_IP_RATE_LIMIT_PER_HOUR:
        _requests[key] = timestamps
        raise APIError(429, "RATE_LIMITED", "Too many requests. Please try again later.")

    timestamps.append(now)
    _requests[key] = timestamps
