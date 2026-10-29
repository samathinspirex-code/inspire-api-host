"""Server-side protections for the public website contact form."""

from ipaddress import ip_address

import httpx

from app.core.config import settings
from app.core.errors import APIError


def canonical_ip(value: str | None) -> str | None:
    try:
        return str(ip_address(value.strip())) if value else None
    except ValueError:
        return None


async def check_contact_submission(token: str | None, visitor_ip: str | None, peer_ip: str | None) -> None:
    blocked = {address for raw in settings.CONTACT_BLOCKED_IPS.split(",") if (address := canonical_ip(raw))}
    if any(address in blocked for address in (canonical_ip(visitor_ip), canonical_ip(peer_ip)) if address):
        raise APIError(403, "CONTACT_BLOCKED", "This contact request cannot be accepted.")

    if not settings.CONTACT_RECAPTCHA_ENABLED:
        return
    if not settings.CONTACT_RECAPTCHA_SECRET_KEY:
        raise APIError(503, "CONTACT_VERIFICATION_UNAVAILABLE", "Contact verification is temporarily unavailable.")
    if not token or len(token) > 4096:
        raise APIError(400, "CAPTCHA_REQUIRED", "Please complete the reCAPTCHA check and try again.")

    data = {"secret": settings.CONTACT_RECAPTCHA_SECRET_KEY, "response": token}
    if address := canonical_ip(visitor_ip):
        data["remoteip"] = address
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post("https://www.google.com/recaptcha/api/siteverify", data=data)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise APIError(503, "CONTACT_VERIFICATION_UNAVAILABLE", "Contact verification is temporarily unavailable.") from exc

    allowed = {name.strip().lower() for name in settings.CONTACT_RECAPTCHA_ALLOWED_HOSTNAMES.split(",") if name.strip()}
    if settings.ENVIRONMENT.lower() != "production":
        allowed.update({"localhost", "127.0.0.1"})
    if not result.get("success") or str(result.get("hostname") or "").lower() not in allowed:
        raise APIError(400, "CAPTCHA_INVALID", "Please complete the reCAPTCHA check again.")
