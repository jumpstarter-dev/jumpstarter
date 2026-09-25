import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from jumpstarter.streams.progress import ProgressStream, _fmt_bytes

pytestmark = pytest.mark.anyio


class _BytesStream:
    def extra(self, attribute, default):
        return default

    async def receive(self):
        return b"x" * 1024

    async def send(self, item):
        pass

    async def send_eof(self):
        pass

    async def aclose(self):
        pass


def test_rich_bar_disabled_when_logging():
    ps = ProgressStream(stream=_BytesStream(), logging=True)
    assert ps._ProgressStream__prog.disable is True


def test_rich_bar_enabled_when_not_logging():
    with patch.dict(os.environ, {"TERM": "xterm"}):
        ps = ProgressStream(stream=_BytesStream(), logging=False)
    assert ps._ProgressStream__prog.disable is False


async def test_logging_emits_plain_text():
    ps = ProgressStream(stream=_BytesStream(), logging=True)
    ps._ProgressStream__last = datetime.now() - timedelta(seconds=10)

    with patch("jumpstarter.streams.progress.logger") as mock_logger:
        await ps.receive()

    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert "━" not in msg  # Rich bar block character
    assert "%" in msg
    assert "elapsed" in msg


async def test_no_logging_skips_log_call():
    with patch.dict(os.environ, {"TERM": "dumb"}):
        ps = ProgressStream(stream=_BytesStream(), logging=False)
    ps._ProgressStream__last = datetime.now() - timedelta(seconds=10)

    with patch("jumpstarter.streams.progress.logger") as mock_logger:
        await ps.receive()

    mock_logger.info.assert_not_called()


async def test_send_without_prior_receive_does_not_crash():
    ps = ProgressStream(stream=_BytesStream(), logging=True)
    await ps.send(b"hello world")


def test_fmt_bytes_adapts_units():
    assert _fmt_bytes(500) == "500 B"
    assert _fmt_bytes(1500) == "1.5 KB"
    assert _fmt_bytes(1_500_000) == "1.5 MB"
    assert _fmt_bytes(1_500_000_000) == "1.5 GB"
