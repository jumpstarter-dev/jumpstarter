import pytest

from .testing import write_fake_appium


@pytest.fixture
def fake_appium(tmp_path, monkeypatch):
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(record))
    return write_fake_appium(tmp_path), record
