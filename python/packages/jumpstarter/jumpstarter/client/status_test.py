import os
from unittest.mock import patch

from jumpstarter.client.status import (
    STATUS_ICONS,
    _use_emoji,
    status_help_text,
    status_icon,
)
from jumpstarter.common.enums import ExporterStatus


class TestUseEmoji:
    """Tests for _use_emoji() terminal detection."""

    def _env_with_term(self, term):
        """Build a clean env dict with only TERM set (no NO_COLOR/NO_ICONS)."""
        env = {k: v for k, v in os.environ.items() if k not in ("TERM", "NO_COLOR", "NO_ICONS")}
        env["TERM"] = term
        return env

    def test_returns_false_when_term_dumb(self):
        with patch.dict("os.environ", self._env_with_term("dumb"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is False

    def test_returns_false_when_term_linux(self):
        with patch.dict("os.environ", self._env_with_term("linux"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is False

    def test_returns_false_when_term_vt100(self):
        with patch.dict("os.environ", self._env_with_term("vt100"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is False

    def test_returns_false_when_term_ansi(self):
        with patch.dict("os.environ", self._env_with_term("ansi"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is False

    def test_returns_false_when_term_unset(self):
        env = {k: v for k, v in os.environ.items() if k not in ("TERM", "NO_COLOR", "NO_ICONS")}
        with patch.dict("os.environ", env, clear=True), patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            assert _use_emoji() is False

    def test_no_color_set_does_not_affect_emoji(self):
        """NO_COLOR only concerns ANSI color sequences (see no-color.org), not icons."""
        with patch.dict("os.environ", {**self._env_with_term("xterm-256color"), "NO_COLOR": ""}, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True

    def test_no_color_set_with_value_does_not_affect_emoji(self):
        with patch.dict("os.environ", {**self._env_with_term("xterm-256color"), "NO_COLOR": "1"}, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True

    def test_returns_false_when_no_icons_set(self):
        with patch.dict("os.environ", {**self._env_with_term("xterm-256color"), "NO_ICONS": ""}, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is False

    def test_returns_false_when_no_icons_set_with_value(self):
        with patch.dict("os.environ", {**self._env_with_term("xterm-256color"), "NO_ICONS": "1"}, clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is False

    def test_returns_false_when_stdout_not_tty(self):
        with patch.dict("os.environ", self._env_with_term("xterm-256color"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = False
                assert _use_emoji() is False

    def test_returns_true_for_xterm(self):
        with patch.dict("os.environ", self._env_with_term("xterm-256color"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True

    def test_returns_true_for_tmux(self):
        with patch.dict("os.environ", self._env_with_term("tmux-256color"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True

    def test_returns_true_for_screen(self):
        with patch.dict("os.environ", self._env_with_term("screen-256color"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True

    def test_returns_true_for_alacritty(self):
        with patch.dict("os.environ", self._env_with_term("alacritty"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True

    def test_returns_true_for_kitty(self):
        with patch.dict("os.environ", self._env_with_term("kitty"), clear=True):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                assert _use_emoji() is True


class TestStatusIcon:
    """Tests for status_icon() with emoji output."""

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_available_shows_green_circle_emoji(self, _mock):
        assert status_icon(ExporterStatus.AVAILABLE) == "🟢"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_offline_shows_cross_emoji(self, _mock):
        assert status_icon(ExporterStatus.OFFLINE) == "❌"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_before_lease_hook_shows_gear_emoji(self, _mock):
        assert status_icon(ExporterStatus.BEFORE_LEASE_HOOK) == "⚙️"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_after_lease_hook_shows_gear_emoji(self, _mock):
        assert status_icon(ExporterStatus.AFTER_LEASE_HOOK) == "⚙️"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_lease_ready_shows_lock_emoji(self, _mock):
        assert status_icon(ExporterStatus.LEASE_READY) == "🔒"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_before_lease_hook_failed_shows_exclamation_emoji(self, _mock):
        assert status_icon(ExporterStatus.BEFORE_LEASE_HOOK_FAILED) == "❗"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_after_lease_hook_failed_shows_exclamation_emoji(self, _mock):
        assert status_icon(ExporterStatus.AFTER_LEASE_HOOK_FAILED) == "❗"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_none_status_shows_question_emoji(self, _mock):
        assert status_icon(None) == "❓"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_unspecified_status_shows_question_emoji(self, _mock):
        assert status_icon(ExporterStatus.UNSPECIFIED) == "❓"


class TestStatusIconAscii:
    """Tests for status_icon() ASCII fallback (NO_ICONS / TERM=dumb / non-TTY)."""

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_available_shows_plus(self, _mock):
        assert status_icon(ExporterStatus.AVAILABLE) == "+"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_offline_shows_x(self, _mock):
        assert status_icon(ExporterStatus.OFFLINE) == "x"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_before_lease_hook_shows_asterisk(self, _mock):
        assert status_icon(ExporterStatus.BEFORE_LEASE_HOOK) == "*"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_after_lease_hook_shows_asterisk(self, _mock):
        assert status_icon(ExporterStatus.AFTER_LEASE_HOOK) == "*"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_lease_ready_shows_tilde(self, _mock):
        assert status_icon(ExporterStatus.LEASE_READY) == "~"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_before_lease_hook_failed_shows_bang(self, _mock):
        assert status_icon(ExporterStatus.BEFORE_LEASE_HOOK_FAILED) == "!"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_after_lease_hook_failed_shows_bang(self, _mock):
        assert status_icon(ExporterStatus.AFTER_LEASE_HOOK_FAILED) == "!"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_none_status_shows_question_mark(self, _mock):
        assert status_icon(None) == "?"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_unspecified_status_shows_question_mark(self, _mock):
        assert status_icon(ExporterStatus.UNSPECIFIED) == "?"


class TestStatusIcons:
    """Tests for the STATUS_ICONS mapping completeness."""

    def test_covers_all_defined_statuses(self):
        """Every ExporterStatus member should have an entry (or fall back to None)."""
        for member in ExporterStatus:
            icon = STATUS_ICONS.get(member, STATUS_ICONS[None])
            assert len(icon) == 3, f"STATUS_ICONS entry for {member} should be a 3-tuple"
            emoji_val, ascii_val, desc = icon
            assert isinstance(emoji_val, str) and len(emoji_val) > 0
            assert isinstance(ascii_val, str) and len(ascii_val) > 0
            assert isinstance(desc, str) and len(desc) > 0

    def test_none_entry_exists(self):
        assert None in STATUS_ICONS

    def test_tuples_have_three_elements(self):
        for key, value in STATUS_ICONS.items():
            assert len(value) == 3, f"STATUS_ICONS[{key}] should be a 3-tuple"


class TestStatusHelpText:
    """Tests for status_help_text()."""

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_ascii_mode_contains_all_ascii_indicators(self, _mock):
        text = status_help_text()
        seen = set()
        for _emoji, ascii_char, desc in STATUS_ICONS.values():
            if (ascii_char, desc) not in seen:
                seen.add((ascii_char, desc))
                assert ascii_char in text, f"Missing '{ascii_char}' in status_help_text()"
                assert desc in text, f"Missing '{desc}' in status_help_text()"

    @patch("jumpstarter.client.status._use_emoji", return_value=True)
    def test_emoji_mode_contains_emoji_indicators(self, _mock):
        text = status_help_text()
        seen = set()
        for emoji_char, _ascii, desc in STATUS_ICONS.values():
            if (emoji_char, desc) not in seen:
                seen.add((emoji_char, desc))
                assert emoji_char in text, f"Missing '{emoji_char}' in status_help_text()"
                assert desc in text, f"Missing '{desc}' in status_help_text()"

    @patch("jumpstarter.client.status._use_emoji", return_value=False)
    def test_is_non_empty_string(self, _mock):
        text = status_help_text()
        assert isinstance(text, str)
        assert len(text) > 0
