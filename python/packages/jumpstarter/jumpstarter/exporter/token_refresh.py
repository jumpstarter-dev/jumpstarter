import math
import time

from jumpstarter.common.jwt import decode_jwt_payload

# Renew long-lived credentials before the controller's 30-day warning window.
DEFAULT_TOKEN_REFRESH_LEAD_TIME: float = 31 * 24 * 3600.0

# Default fraction of total lifetime at which to trigger token refresh (20%).
DEFAULT_TOKEN_REFRESH_FRACTION: float = 0.2

# Minimum lead time before expiry to attempt token refresh (60 seconds).
MIN_TOKEN_REFRESH_LEAD_TIME: float = 60.0


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


def calculate_token_refresh_sleep(
    token: str,
    now: float | None = None,
    lead_time: float = DEFAULT_TOKEN_REFRESH_LEAD_TIME,
    fraction: float = DEFAULT_TOKEN_REFRESH_FRACTION,
    min_lead_time: float = MIN_TOKEN_REFRESH_LEAD_TIME,
) -> float | None:
    """Calculate the number of seconds to sleep before requesting a new token.

    When the token has expired or is within the refresh threshold, returns 0.0
    indicating a refresh should be attempted immediately.

    Args:
        token: JWT string
        now: Optional current timestamp (defaults to time.time())
        lead_time: Maximum lead time before expiry to refresh (default: 31 days)
        fraction: Fraction of total lifetime remaining to trigger refresh (default: 0.2)
        min_lead_time: Minimum lead time before expiry to attempt refresh (default: 60s)

    Returns:
        float >= 0: Seconds to sleep before refreshing (0.0 means refresh now)
        None: Token is invalid or does not have an 'exp' claim (no refresh needed)
    """
    if now is None:
        now = time.time()

    try:
        payload = decode_jwt_payload(token)
    except ValueError:
        return None

    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        return None
    exp_f = float(exp)

    remaining = exp_f - now
    if remaining <= 0:
        return 0.0

    iat = payload.get("iat")
    if isinstance(iat, (int, float)) and not isinstance(iat, bool) and math.isfinite(iat) and float(iat) < exp_f:
        total_lifetime = exp_f - float(iat)
        lead = min(lead_time, max(total_lifetime * fraction, min_lead_time))
        # Ensure lead time never exceeds half the token's total lifetime
        lead = min(lead, total_lifetime * 0.5)
    else:
        lead = min(lead_time, max(remaining * fraction, min_lead_time))
        lead = min(lead, remaining * 0.5)

    sleep_seconds = remaining - lead
    return max(0.0, sleep_seconds)
