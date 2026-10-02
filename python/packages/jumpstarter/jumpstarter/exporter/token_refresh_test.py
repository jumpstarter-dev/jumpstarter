import base64
import json

import pytest

from jumpstarter.common.jwt import decode_jwt_payload
from jumpstarter.exporter.token_refresh import calculate_token_refresh_sleep


def _make_jwt(payload: dict) -> str:
    header = {"alg": "ES256", "typ": "JWT"}
    h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{h_b64}.{p_b64}.dummy_signature"


def test_decode_jwt_payload_valid():
    payload = {"sub": "exporter-1", "exp": 1234567890}
    token = _make_jwt(payload)
    decoded = decode_jwt_payload(token)
    assert decoded["sub"] == "exporter-1"
    assert decoded["exp"] == 1234567890


def test_decode_jwt_payload_invalid():
    with pytest.raises(ValueError, match="expected 3 parts"):
        decode_jwt_payload("invalid.token")

    with pytest.raises(ValueError, match="Failed to decode"):
        decode_jwt_payload("part1.!!!notbase64!!!.part3")


def test_calculate_token_refresh_sleep_365_days():
    # 365 days token: 31,536,000s
    now = 1_000_000.0
    iat = now
    exp = now + 365 * 24 * 3600
    token = _make_jwt({"iat": iat, "exp": exp})

    # Lead time is capped by DEFAULT_TOKEN_REFRESH_LEAD_TIME (31 days)
    # Expected sleep: 365 days - 31 days = 334 days
    sleep_s = calculate_token_refresh_sleep(token, now=now)
    assert sleep_s is not None
    assert pytest.approx(sleep_s, rel=1e-3) == (365 - 31) * 24 * 3600


def test_calculate_token_refresh_sleep_fraction():
    # 10 days token: 20% is 2 days (172,800s), which is < 31 days
    now = 1_000_000.0
    iat = now
    exp = now + 10 * 24 * 3600
    token = _make_jwt({"iat": iat, "exp": exp})

    # Lead time = 2 days
    # Expected sleep: 8 days
    sleep_s = calculate_token_refresh_sleep(token, now=now)
    assert sleep_s is not None
    assert pytest.approx(sleep_s, rel=1e-3) == 8 * 24 * 3600


def test_calculate_token_refresh_sleep_short_token():
    # 100 seconds token: 50% cap applies (lead = 50s)
    now = 1_000_000.0
    iat = now
    exp = now + 100
    token = _make_jwt({"iat": iat, "exp": exp})

    # Expected sleep: 50s
    sleep_s = calculate_token_refresh_sleep(token, now=now)
    assert sleep_s is not None
    assert pytest.approx(sleep_s, rel=1e-3) == 50.0


def test_calculate_token_refresh_sleep_near_expiry():
    # Token expiring in 3 days (less than 31 days lead time on a 365-day token)
    now = 1_000_000.0
    iat = now - 362 * 24 * 3600
    exp = now + 3 * 24 * 3600
    token = _make_jwt({"iat": iat, "exp": exp})

    sleep_s = calculate_token_refresh_sleep(token, now=now)
    assert sleep_s == 0.0


def test_calculate_token_refresh_sleep_expired():
    now = 1_000_000.0
    exp = now - 100
    token = _make_jwt({"exp": exp})

    sleep_s = calculate_token_refresh_sleep(token, now=now)
    assert sleep_s == 0.0


def test_calculate_token_refresh_sleep_no_exp_or_invalid():
    token_no_exp = _make_jwt({"sub": "test"})
    assert calculate_token_refresh_sleep(token_no_exp) is None
    assert calculate_token_refresh_sleep("not-a-token") is None


@pytest.mark.parametrize("days, warning_days", [(365, 30), (30, 3), (1, 0.1)])
def test_renewal_precedes_exporter_warning_window(days, warning_days):
    now = 1_000_000.0
    lifetime = days * 86400
    token = _make_jwt({"iat": now, "exp": now + lifetime})
    sleep = calculate_token_refresh_sleep(token, now=now)
    assert lifetime - sleep > warning_days * 86400


@pytest.mark.parametrize("claims", [[], "text", None])
def test_decode_rejects_non_object_payload(claims):
    with pytest.raises(ValueError, match="payload must be an object"):
        decode_jwt_payload(_make_jwt(claims))


@pytest.mark.parametrize("expiry", [True, "1000", float("nan"), float("inf")])
def test_refresh_ignores_invalid_expiry(expiry):
    assert calculate_token_refresh_sleep(_make_jwt({"exp": expiry}), now=100) is None
