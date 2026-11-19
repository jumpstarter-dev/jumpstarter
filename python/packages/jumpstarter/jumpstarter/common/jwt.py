import base64
import json
from typing import Any


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Read unverified claims for scheduling and issuer discovery, never authentication."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid JWT format: expected 3 parts, got {len(parts)}")
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
        if not isinstance(claims, dict):
            raise TypeError("JWT payload must be an object")
        return claims
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid JWT format: Failed to decode JWT payload: {e}") from e
