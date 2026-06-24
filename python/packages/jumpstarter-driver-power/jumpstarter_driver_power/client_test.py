import logging
import time

from click.testing import CliRunner

from .driver import MockPower
from jumpstarter.common.utils import serve


def test_log_stream(caplog):
    """Test that driver logs are properly streamed to the client."""
    with serve(MockPower()) as client:
        # Set log level to capture INFO messages from exporter:driver logger
        with caplog.at_level(logging.INFO, logger="exporter:driver"):
            with client.log_stream():
                client.on()
                time.sleep(1)  # to ensure log is flushed
                assert "power on" in caplog.text

                client.off()
                time.sleep(1)
                assert "power off" in caplog.text


def test_read_values():
    with serve(MockPower()) as client:
        readings = list(client.read())
        assert [(r.voltage, r.current) for r in readings] == [(0.0, 0.0), (5.0, 2.0)]
        assert readings[1].apparent_power == 10.0


def test_read_cli():
    with serve(MockPower()) as client:
        result = CliRunner().invoke(client.cli(), ["read"])
        assert result.exit_code == 0
        assert "voltage=0.0 V" in result.output
        assert "voltage=5.0 V  current=2.0 A  apparent_power=10.0 VA" in result.output
