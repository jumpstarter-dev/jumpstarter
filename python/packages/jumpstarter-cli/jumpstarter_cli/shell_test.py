import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jumpstarter_cli.shell import (
    _attempt_token_recovery,
    _monitor_token_expiry,
    _try_refresh_token,
    _try_reload_token_from_disk,
    _update_lease_channel,
    _warn_refresh_failed,
)

pytestmark = pytest.mark.anyio


def _make_config(token="tok", refresh_token="rt", path="/tmp/config.yaml"):
    """Create a mock config with sensible defaults."""
    config = Mock()
    config.token = token
    config.refresh_token = refresh_token
    config.path = path
    config.channel = AsyncMock(return_value=Mock(name="new_channel"))
    return config


def _make_lease():
    """Create a mock lease with a refresh_channel method."""
    lease = Mock()
    lease.refresh_channel = Mock()
    return lease


class TestUpdateLeaseChannel:
    async def test_updates_channel_on_lease(self):
        config = _make_config()
        lease = _make_lease()

        await _update_lease_channel(config, lease)

        config.channel.assert_awaited_once()
        lease.refresh_channel.assert_called_once_with(config.channel.return_value)

    async def test_noop_when_lease_is_none(self):
        config = _make_config()

        await _update_lease_channel(config, None)

        config.channel.assert_not_awaited()


class TestTryRefreshToken:
    async def test_returns_false_when_no_refresh_token(self):
        config = _make_config(refresh_token=None)
        assert await _try_refresh_token(config, _make_lease()) is False

    async def test_returns_false_when_refresh_token_is_empty(self):
        config = _make_config(refresh_token="")
        assert await _try_refresh_token(config, _make_lease()) is False

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    @patch("jumpstarter_cli.shell.Config")
    @patch("jumpstarter_cli.shell.decode_jwt_issuer", return_value="https://issuer")
    async def test_successful_refresh(self, _mock_issuer, mock_oidc_cls, mock_save):
        config = _make_config()
        lease = _make_lease()

        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.return_value = {
            "access_token": "new_tok",
            "refresh_token": "new_rt",
        }
        mock_oidc_cls.return_value = mock_oidc

        result = await _try_refresh_token(config, lease)

        assert result is True
        assert config.token == "new_tok"
        assert config.refresh_token == "new_rt"
        lease.refresh_channel.assert_called_once()
        mock_save.save.assert_called_once()

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    @patch("jumpstarter_cli.shell.Config")
    @patch("jumpstarter_cli.shell.decode_jwt_issuer", return_value="https://issuer")
    async def test_successful_refresh_without_new_refresh_token(
        self, _mock_issuer, mock_oidc_cls, _mock_save
    ):
        config = _make_config()
        lease = _make_lease()

        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.return_value = {
            "access_token": "new_tok",
            # No refresh_token in response
        }
        mock_oidc_cls.return_value = mock_oidc

        result = await _try_refresh_token(config, lease)

        assert result is True
        assert config.token == "new_tok"
        assert config.refresh_token == "rt"  # unchanged

    @patch("jumpstarter_cli.shell.decode_jwt_issuer", side_effect=ValueError("bad jwt"))
    async def test_rollback_on_failure(self, _mock_issuer):
        config = _make_config(token="original_tok", refresh_token="original_rt")
        lease = _make_lease()

        result = await _try_refresh_token(config, lease)

        assert result is False
        assert config.token == "original_tok"
        assert config.refresh_token == "original_rt"
        lease.refresh_channel.assert_not_called()

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    @patch("jumpstarter_cli.shell.Config")
    @patch("jumpstarter_cli.shell.decode_jwt_issuer", return_value="https://issuer")
    async def test_save_failure_does_not_fail_refresh(
        self, _mock_issuer, mock_oidc_cls, mock_save, caplog
    ):
        """Disk save is best-effort; refresh should still succeed."""
        config = _make_config()
        lease = _make_lease()

        mock_oidc = AsyncMock()
        mock_oidc.refresh_token_grant.return_value = {
            "access_token": "new_tok",
        }
        mock_oidc_cls.return_value = mock_oidc
        mock_save.save.side_effect = OSError("disk full")

        with caplog.at_level(logging.WARNING):
            result = await _try_refresh_token(config, lease)

        assert result is True
        assert config.token == "new_tok"
        assert "Failed to save refreshed token to disk" in caplog.text


class TestTryReloadTokenFromDisk:
    async def test_returns_false_when_no_path(self):
        config = _make_config(path=None)
        assert await _try_reload_token_from_disk(config, _make_lease()) is False

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds", return_value=3600)
    async def test_successful_reload(self, _mock_remaining, mock_client_cfg):
        config = _make_config(token="old_tok", refresh_token="old_rt")
        lease = _make_lease()

        disk_config = Mock()
        disk_config.token = "disk_tok"
        disk_config.refresh_token = "disk_rt"
        mock_client_cfg.from_file.return_value = disk_config

        result = await _try_reload_token_from_disk(config, lease)

        assert result is True
        assert config.token == "disk_tok"
        assert config.refresh_token == "disk_rt"
        lease.refresh_channel.assert_called_once()

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    async def test_returns_false_when_disk_token_is_same(self, mock_client_cfg):
        config = _make_config(token="same_tok")
        disk_config = Mock()
        disk_config.token = "same_tok"
        mock_client_cfg.from_file.return_value = disk_config

        result = await _try_reload_token_from_disk(config, _make_lease())

        assert result is False

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds", return_value=-10)
    async def test_returns_false_when_disk_token_is_expired(
        self, _mock_remaining, mock_client_cfg
    ):
        config = _make_config(token="old_tok")
        disk_config = Mock()
        disk_config.token = "disk_tok"
        mock_client_cfg.from_file.return_value = disk_config

        result = await _try_reload_token_from_disk(config, _make_lease())

        assert result is False
        assert config.token == "old_tok"  # unchanged

    @patch("jumpstarter_cli.shell.ClientConfigV1Alpha1")
    async def test_rollback_on_file_error(self, mock_client_cfg):
        config = _make_config(token="orig_tok", refresh_token="orig_rt")
        mock_client_cfg.from_file.side_effect = FileNotFoundError("gone")

        result = await _try_reload_token_from_disk(config, _make_lease())

        assert result is False
        assert config.token == "orig_tok"
        assert config.refresh_token == "orig_rt"


class TestAttemptTokenRecovery:
    @patch("jumpstarter_cli.shell._try_reload_token_from_disk", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._try_refresh_token", new_callable=AsyncMock)
    async def test_returns_message_on_oidc_success(self, mock_refresh, mock_disk):
        mock_refresh.return_value = True

        result = await _attempt_token_recovery(Mock(), Mock(), 60)

        assert result == "Token refreshed automatically."
        mock_disk.assert_not_awaited()  # should not fall through

    @patch("jumpstarter_cli.shell._try_reload_token_from_disk", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._try_refresh_token", new_callable=AsyncMock)
    async def test_falls_back_to_disk_reload(self, mock_refresh, mock_disk):
        mock_refresh.return_value = False
        mock_disk.return_value = True

        result = await _attempt_token_recovery(Mock(), Mock(), 60)

        assert result == "Token reloaded from login."

    @patch("jumpstarter_cli.shell._try_reload_token_from_disk", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._try_refresh_token", new_callable=AsyncMock)
    async def test_returns_none_when_all_fail(self, mock_refresh, mock_disk):
        mock_refresh.return_value = False
        mock_disk.return_value = False

        result = await _attempt_token_recovery(Mock(), Mock(), 60)

        assert result is None


class TestWarnRefreshFailed:
    @patch("jumpstarter_cli.shell.click")
    def test_warns_yellow_when_time_remaining(self, mock_click):
        _warn_refresh_failed(300)
        mock_click.style.assert_called_once()
        _, kwargs = mock_click.style.call_args
        assert kwargs["fg"] == "yellow"

    @patch("jumpstarter_cli.shell.click")
    def test_warns_red_when_expired(self, mock_click):
        _warn_refresh_failed(-10)
        mock_click.style.assert_called_once()
        _, kwargs = mock_click.style.call_args
        assert kwargs["fg"] == "red"


class TestMonitorTokenExpiry:
    async def test_returns_immediately_when_no_token(self):
        config = Mock(spec=[])  # no token attribute
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, None, cancel_scope)
        # Should return without error

    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds", return_value=None)
    async def test_returns_when_remaining_is_none(self, _mock_remaining, _mock_sleep):
        config = _make_config()
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, None, cancel_scope)

    @patch("jumpstarter_cli.shell.click")
    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._attempt_token_recovery", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds")
    async def test_refreshes_when_below_threshold(
        self, mock_remaining, mock_recovery, mock_sleep, mock_click
    ):
        # First call: below threshold; second call: raise to exit
        mock_remaining.side_effect = [60, Exception("done")]
        mock_recovery.return_value = "Token refreshed automatically."
        config = _make_config()
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, _make_lease(), cancel_scope)

        mock_recovery.assert_awaited_once()
        # Should print the green success message
        mock_click.echo.assert_called()

    @patch("jumpstarter_cli.shell.click")
    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._attempt_token_recovery", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds")
    async def test_warns_when_refresh_fails(
        self, mock_remaining, mock_recovery, mock_sleep, mock_click
    ):
        mock_remaining.side_effect = [60, Exception("done")]
        mock_recovery.return_value = None  # all recovery failed
        config = _make_config()
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, _make_lease(), cancel_scope)

        mock_recovery.assert_awaited_once()

    @patch("jumpstarter_cli.shell.click")
    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds")
    async def test_warns_within_expiry_window(
        self, mock_remaining, mock_sleep, mock_click
    ):
        from jumpstarter_cli_common.oidc import TOKEN_EXPIRY_WARNING_SECONDS

        # First iteration: within warning window but above refresh threshold
        # Second iteration: exit via exception
        mock_remaining.side_effect = [
            TOKEN_EXPIRY_WARNING_SECONDS - 10,
            Exception("done"),
        ]
        config = _make_config()
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, _make_lease(), cancel_scope)

        # Verify warning was echoed
        mock_click.echo.assert_called()
        args = mock_click.style.call_args
        assert "auto-refresh" in args[0][0]

    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds", return_value=500)
    async def test_sleeps_30s_when_above_threshold(self, _mock_remaining, mock_sleep):
        # Exit after one loop via cancel_called
        call_count = 0

        def check_cancelled():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        config = _make_config()
        cancel_scope = Mock()
        type(cancel_scope).cancel_called = property(lambda self: check_cancelled())

        await _monitor_token_expiry(config, _make_lease(), cancel_scope)

        mock_sleep.assert_awaited_with(30)

    @patch("jumpstarter_cli.shell.click")
    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._attempt_token_recovery", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds")
    async def test_sleeps_5s_when_below_threshold(
        self, mock_remaining, mock_recovery, mock_sleep, _mock_click
    ):
        mock_remaining.side_effect = [60, Exception("done")]
        mock_recovery.return_value = None
        config = _make_config()
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, _make_lease(), cancel_scope)

        mock_sleep.assert_awaited_with(5)

    @patch("jumpstarter_cli.shell.click")
    @patch("jumpstarter_cli.shell.anyio.sleep", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell._attempt_token_recovery", new_callable=AsyncMock)
    @patch("jumpstarter_cli.shell.get_token_remaining_seconds")
    async def test_does_not_cancel_scope_on_expiry(
        self, mock_remaining, mock_recovery, mock_sleep, _mock_click
    ):
        """The monitor must never cancel the scope — the shell stays alive."""
        mock_remaining.side_effect = [60, Exception("done")]
        mock_recovery.return_value = None
        config = _make_config()
        cancel_scope = Mock(cancel_called=False)

        await _monitor_token_expiry(config, _make_lease(), cancel_scope)

        cancel_scope.cancel.assert_not_called()
