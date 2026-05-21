import base64
import json
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from jumpstarter_cli.auth import auth


def _make_jwt(exp_offset_seconds=3600, sub="test-subject", iss="https://localhost:8085"):
    """Create a fake JWT with given expiry offset from now."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    payload_data = {
        "sub": sub,
        "iss": iss,
        "exp": int(time.time()) + exp_offset_seconds,
        "iat": int(time.time()),
    }
    payload = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.fake-signature"


def _make_expired_jwt():
    return _make_jwt(exp_offset_seconds=-3600)


def _mock_config(token=None, refresh_token=None, rotate_return=None):
    config = MagicMock()
    config.token = token
    config.refresh_token = refresh_token
    if rotate_return is not None:
        config.rotate_token = AsyncMock(return_value=rotate_return)
    return config


def _patch_config(config):
    """Patch ClientConfigV1Alpha1 at all import sites so opt_config returns our mock."""
    mock_cls = MagicMock()
    mock_cls.return_value = config
    mock_cls.load.return_value = config
    mock_cls.from_file.return_value = config
    mock_cls.save = MagicMock()
    stack = ExitStack()
    stack.enter_context(patch("jumpstarter_cli_common.config.ClientConfigV1Alpha1", mock_cls))
    stack.enter_context(patch("jumpstarter_cli.auth.ClientConfigV1Alpha1", mock_cls))
    return stack


class TestAuthStatus:
    def setup_method(self):
        self.runner = CliRunner()

    def test_status_no_token(self):
        config = _mock_config(token=None)
        with _patch_config(config):
            result = self.runner.invoke(auth, ["status"])
        assert result.exit_code == 0
        assert "No token found" in result.output

    def test_status_valid_token(self):
        token = _make_jwt(exp_offset_seconds=7200)
        config = _mock_config(token=token)
        with _patch_config(config):
            result = self.runner.invoke(auth, ["status"])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_status_expired_token(self):
        token = _make_expired_jwt()
        config = _mock_config(token=token)
        with _patch_config(config):
            result = self.runner.invoke(auth, ["status"])
        assert result.exit_code == 0
        assert "EXPIRED" in result.output

    def test_status_verbose(self):
        token = _make_jwt(exp_offset_seconds=7200)
        config = _mock_config(token=token, refresh_token="fake-refresh")
        with _patch_config(config):
            result = self.runner.invoke(auth, ["status", "-v"])
        assert result.exit_code == 0
        assert "Refresh token stored: yes" in result.output


class TestAuthRotate:
    def setup_method(self):
        self.runner = CliRunner()

    def test_rotate_no_token(self):
        config = _mock_config(token=None)
        with _patch_config(config):
            result = self.runner.invoke(auth, ["rotate"])
        assert result.exit_code != 0
        assert "No token found" in result.output

    def test_rotate_expired_token(self):
        token = _make_expired_jwt()
        config = _mock_config(token=token)
        with _patch_config(config):
            result = self.runner.invoke(auth, ["rotate"])
        assert result.exit_code != 0
        assert "expired" in result.output.lower()

    def test_rotate_success(self):
        token = _make_jwt(exp_offset_seconds=86400)
        new_token = _make_jwt(exp_offset_seconds=86400 * 365, sub="test-subject")
        config = _mock_config(token=token, rotate_return=new_token)
        with _patch_config(config):
            result = self.runner.invoke(auth, ["rotate"])
        assert result.exit_code == 0
        assert "rotated" in result.output.lower()
        assert config.token == new_token
