import hashlib
import base64
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import jwt

from app.core.config import settings


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_hex(32)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_code(secret: str, step: int) -> str:
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def verify_totp(
    secret: str,
    code: str,
    last_used_step: int | None = None,
    valid_window: int = 1,
    now: float | None = None,
) -> int | None:
    current_step = int((time.time() if now is None else now) // 30)
    for step in range(current_step - valid_window, current_step + valid_window + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(_totp_code(secret, step), code):
            return step
    return None


def generate_recovery_codes(count: int = 10) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        codes.append("-".join(raw[index : index + 4] for index in range(0, 12, 4)))
    return codes


def normalize_recovery_code(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


@lru_cache
def _private_key() -> str:
    return Path(settings.JWT_PRIVATE_KEY_PATH).read_text()


@lru_cache
def _public_key() -> str:
    return Path(settings.JWT_PUBLIC_KEY_PATH).read_text()


def create_access_token(user_id: int, email: str, access: list[str]) -> tuple[str, int]:
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "access": access,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, _private_key(), algorithm="RS256")
    return token, expires_in


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, _public_key(), algorithms=["RS256"])
