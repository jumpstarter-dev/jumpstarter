import math
import time
from typing import Any

from jumpstarter.common.jwt import decode_jwt_payload

# Renew long-lived credentials before the controller's 30-day warning window.
TOKEN_REFRESH_MAX_LEAD_TIME: float = 31 * 24 * 3600.0

TOKEN_REFRESH_FRACTION: float = 0.2

TOKEN_REFRESH_MIN_LEAD_TIME: float = 60.0


def is_internal_exporter_token(token: str) -> bool:
    try:
        claims = decode_jwt_payload(token)
    except ValueError:
        return False
    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or not issuer or not isinstance(subject, str):
        return False
    parts = subject.split(":")
    return len(parts) == 4 and parts[0] == "exporter" and all(parts[1:])


def _numeric_claim(claims: dict[str, Any], name: str) -> float | None:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def calculate_token_refresh_sleep(token: str, now: float | None = None) -> float | None:
    """Calculate the number of seconds to sleep before requesting a new token.

    When the token has expired or is within the refresh threshold, returns 0.0
    indicating a refresh should be attempted immediately.

    Returns:
        float >= 0: Seconds to sleep before refreshing (0.0 means refresh now)
        None: Token is invalid or does not have an 'exp' claim (no refresh needed)
    """
    if now is None:
        now = time.time()
    try:
        claims = decode_jwt_payload(token)
    except ValueError:
        return None
    exp = _numeric_claim(claims, "exp")
    if exp is None:
        return None
    remaining = exp - now
    if remaining <= 0:
        return 0.0
    iat = _numeric_claim(claims, "iat")
    lifetime = exp - iat if iat is not None and iat < exp else remaining
    lead = min(
        TOKEN_REFRESH_MAX_LEAD_TIME,
        max(lifetime * TOKEN_REFRESH_FRACTION, TOKEN_REFRESH_MIN_LEAD_TIME),
        lifetime / 2,
    )
    return max(0.0, remaining - lead)


def calculate_token_rotation_retry_sleep(token: str, max_interval: float, now: float | None = None) -> float:
    if now is None:
        now = time.time()
    try:
        exp = _numeric_claim(decode_jwt_payload(token), "exp")
    except ValueError:
        return max_interval
    if exp is None:
        return max_interval
    return min(max_interval, max(0.0, (exp - now) / 2))
