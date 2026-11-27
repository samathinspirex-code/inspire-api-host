"""Feature switches and per-visitor rate limits for the new website routes.

The website calls the API from its own server, so the network address is the website's. When the request carries
X-Web-Proxy-Key equal to WEB_PROXY_SECRET, the visitor address in X-Web-Visitor-IP is trusted for rate limiting.
"""
import hashlib
import hmac
import time
from collections import OrderedDict

from fastapi import Request

from app.core.config import settings
from app.core.errors import APIError

_windows: OrderedDict = OrderedDict()


def visitor_ip(request: Request) -> str:
    key = request.headers.get("x-web-proxy-key", "")
    if settings.WEB_PROXY_SECRET and key and hmac.compare_digest(key, settings.WEB_PROXY_SECRET):
        ip = request.headers.get("x-web-visitor-ip", "").split(",")[0].strip()[:64]
        if ip:
            return ip
    return request.client.host if request.client else "unknown"


def limit(request: Request, bucket: str, per_window: int, window_seconds: int = 60) -> None:
    now = int(time.monotonic() // window_seconds)
    key = hashlib.sha256(f"{bucket}:{window_seconds}:{visitor_ip(request)}".encode()).digest()
    window, count = _windows.pop(key, (now, 0))
    count = count + 1 if window == now else 1
    _windows[key] = (now, count)
    while len(_windows) > 20000:
        _windows.popitem(last=False)
    if count > per_window:
        raise APIError(429, "RATE_LIMITED", "Too many attempts. Please wait a few minutes and try again.")


def require(flag: str):
    async def _check():
        if not getattr(settings, flag, False):
            raise APIError(503, "WEB_FEATURE_OFF", "This part of the website is not switched on yet.")
    return _check
