import dataclasses

import pytest

from .display import DisplayOptions, display_options


class TestDisplayOptions:
    def test_defaults_when_unset(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("NO_ICONS", raising=False)
        opts = display_options()
        assert opts.no_color is False
        assert opts.no_icons is False

    def test_no_color_set_empty(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        monkeypatch.delenv("NO_ICONS", raising=False)
        opts = display_options()
        assert opts.no_color is True
        assert opts.no_icons is False

    def test_no_color_set_with_value(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.delenv("NO_ICONS", raising=False)
        opts = display_options()
        assert opts.no_color is True
        assert opts.no_icons is False

    def test_no_icons_set_empty(self, monkeypatch):
        monkeypatch.setenv("NO_ICONS", "")
        monkeypatch.delenv("NO_COLOR", raising=False)
        opts = display_options()
        assert opts.no_color is False
        assert opts.no_icons is True

    def test_no_icons_set_with_value(self, monkeypatch):
        monkeypatch.setenv("NO_ICONS", "1")
        monkeypatch.delenv("NO_COLOR", raising=False)
        opts = display_options()
        assert opts.no_color is False
        assert opts.no_icons is True

    def test_both_set(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("NO_ICONS", "1")
        opts = display_options()
        assert opts.no_color is True
        assert opts.no_icons is True

    def test_options_are_immutable(self):
        opts = DisplayOptions(no_color=True, no_icons=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            opts.no_color = False
