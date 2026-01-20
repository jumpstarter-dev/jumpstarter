import logging
import time
from unittest.mock import MagicMock

from .driver import MockPower
from jumpstarter.common.utils import serve


def test_log_stream(monkeypatch):
    with serve(MockPower()) as client:
        log = MagicMock()
        monkeypatch.setattr(client, "_AsyncDriverClient__log", log)
        with client.log_stream():
            client.on()
            time.sleep(1)  # to ensure log is flushed
            log.assert_called_with(logging.INFO, "power on")

            client.off()
            time.sleep(1)
            log.assert_called_with(logging.INFO, "power off")
