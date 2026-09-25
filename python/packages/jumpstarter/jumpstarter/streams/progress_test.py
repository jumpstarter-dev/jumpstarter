import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from anyio.abc import ObjectStream

from jumpstarter.streams.progress import ProgressStream, _fmt_bytes

pytestmark = pytest.mark.anyio


class _BytesStream(ObjectStream[bytes]):
    def extra(self, attribute, default=None):
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
    assert ps._ProgressStream__prog.disable is True  # type: ignore[attr-defined]


def test_rich_bar_enabled_when_not_logging():
    with patch.dict(os.environ, {"TERM": "xterm"}):
        ps = ProgressStream(stream=_BytesStream(), logging=False)
    assert ps._ProgressStream__prog.disable is False  # type: ignore[attr-defined]


async def test_logging_emits_plain_text():
    ps = ProgressStream(stream=_BytesStream(), logging=True)
    ps._ProgressStream__last = datetime.now(tz=UTC) - timedelta(seconds=10)  # type: ignore[attr-defined]

    with patch("jumpstarter.streams.progress.logger") as mock_logger:
        await ps.receive()

    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert "━" not in msg
    assert "elapsed" in msg


async def test_no_logging_skips_log_call():
    with patch.dict(os.environ, {"TERM": "dumb"}):
        ps = ProgressStream(stream=_BytesStream(), logging=False)
    ps._ProgressStream__last = datetime.now(tz=UTC) - timedelta(seconds=10)  # type: ignore[attr-defined]

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


def test_log_progress_unknown_total_shows_question_mark():
    from rich.progress import Progress, TextColumn

    from jumpstarter.streams.progress import _log_progress

    p = Progress(TextColumn("{task.description}"), disable=True)
    p.start()
    tid = p.add_task("transfer", total=None)
    p.advance(tid, 512 * 1024)
    msg = _log_progress(p.tasks[tid])
    p.stop()

    assert "0.0%" not in msg
    assert msg.startswith("transfer: ?")


def test_log_progress_known_total_shows_percentage():
    from rich.progress import Progress, TextColumn

    from jumpstarter.streams.progress import _log_progress

    p = Progress(TextColumn("{task.description}"), disable=True)
    p.start()
    tid = p.add_task("transfer", total=1_000_000)
    p.advance(tid, 500_000)
    msg = _log_progress(p.tasks[tid])
    p.stop()

    assert "50.0%" in msg
