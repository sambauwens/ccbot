"""Tests for session_map key extraction — session-per-instance model."""

import pytest

from ccbot.session import SessionManager


class TestExtractWindowId:
    """Test _extract_window_id_from_key for various key formats."""

    @pytest.fixture
    def sm(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
        monkeypatch.setenv("ALLOWED_USERS", "12345")
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        return SessionManager()

    def test_standard_key(self, sm):
        assert sm._extract_window_id_from_key("france-2026:@5") == "@5"

    def test_old_ccbot_key(self, sm):
        assert sm._extract_window_id_from_key("ccbot:@12") == "@12"

    def test_session_name_with_numbers(self, sm):
        assert sm._extract_window_id_from_key("france-2026-2:@0") == "@0"

    def test_no_window_id(self, sm):
        assert sm._extract_window_id_from_key("ccbot:my-window") is None

    def test_just_window_id(self, sm):
        # Edge case: key is just "@5" (no session name prefix)
        assert sm._extract_window_id_from_key("@5") is None

    def test_empty_key(self, sm):
        assert sm._extract_window_id_from_key("") is None
