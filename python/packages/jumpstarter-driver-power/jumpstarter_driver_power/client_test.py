import logging
import time

from click.testing import CliRunner

from .driver import MockPower, PowerInterface
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


class NoStatusPower(PowerInterface, Driver):
    """A power driver that, like most, has no status export."""

    @export
    def on(self) -> None: ...

    @export
    def off(self) -> None: ...

    @export
    def read(self):
        yield from ()


def test_log_stream(caplog):
    """Test that driver logs are properly streamed to the client."""
    with (
        serve(MockPower()) as client,
        caplog.at_level(logging.INFO, logger="exporter:driver"),
        client.log_stream(),
    ):
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


def test_status_cli():
    with serve(MockPower()) as client:
        runner = CliRunner()
        assert runner.invoke(client.cli(), ["status"]).output.strip() == "off"
        client.on()
        assert runner.invoke(client.cli(), ["status"]).output.strip() == "on"


def test_status_is_none_when_the_driver_has_no_status():
    with serve(NoStatusPower()) as client:
        assert client.status() is None
        result = CliRunner().invoke(client.cli(), ["status"])
        assert result.exit_code != 0
        assert "does not report its state" in result.output
