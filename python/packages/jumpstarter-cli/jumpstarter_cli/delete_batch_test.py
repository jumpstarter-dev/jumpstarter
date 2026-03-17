from unittest.mock import Mock

import click
import pytest

from jumpstarter_cli.delete import delete_leases


class TestBatchDeleteLeases:
    def test_delete_multiple_leases(self):
        config = Mock()
        delete_leases.callback.__wrapped__.__wrapped__(
            config=config,
            names=("lease1", "lease2", "lease3"),
            selector=None,
            all=False,
            output=None,
        )
        assert config.delete_lease.call_count == 3
        config.delete_lease.assert_any_call(name="lease1")
        config.delete_lease.assert_any_call(name="lease2")
        config.delete_lease.assert_any_call(name="lease3")

    def test_delete_zero_names_no_flags_raises_error(self):
        config = Mock()
        with pytest.raises(click.ClickException, match="must be specified"):
            delete_leases.callback.__wrapped__.__wrapped__(
                config=config,
                names=(),
                selector=None,
                all=False,
                output=None,
            )

    def test_delete_with_output_name(self):
        from jumpstarter_cli_common.opt import OutputMode

        config = Mock()
        delete_leases.callback.__wrapped__.__wrapped__(
            config=config,
            names=("lease1", "lease2"),
            selector=None,
            all=False,
            output=OutputMode.NAME,
        )
        assert config.delete_lease.call_count == 2
