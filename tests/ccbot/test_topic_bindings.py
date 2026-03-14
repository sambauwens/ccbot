"""Tests for topic-level bindings (conversational topics)."""

import pytest

from ccbot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestTopicBindings:
    def test_bind_and_get(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1")
        assert mgr.get_window_for_topic(-100123, 42) == "@1"

    def test_bind_general_topic(self, mgr: SessionManager) -> None:
        """General topic (thread_id=None) maps to key with 0."""
        mgr.bind_topic(-100123, None, "@2")
        assert mgr.get_window_for_topic(-100123, None) == "@2"

    def test_unbind_returns_window_id(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1")
        result = mgr.unbind_topic(-100123, 42)
        assert result == "@1"
        assert mgr.get_window_for_topic(-100123, 42) is None

    def test_unbind_nonexistent_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.unbind_topic(-100123, 999) is None

    def test_get_unbound_returns_none(self, mgr: SessionManager) -> None:
        assert mgr.get_window_for_topic(-100123, 42) is None

    def test_topic_type_stored(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1", topic_type="conversational")
        assert mgr.get_topic_type(-100123, 42) == "conversational"

    def test_topic_type_dev(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1", topic_type="dev")
        assert mgr.get_topic_type(-100123, 42) == "dev"

    def test_topic_type_cleared_on_unbind(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1", topic_type="conversational")
        mgr.unbind_topic(-100123, 42)
        assert mgr.get_topic_type(-100123, 42) is None

    def test_iter_topic_bindings(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1")
        mgr.bind_topic(-100456, None, "@2")
        result = set(mgr.iter_topic_bindings())
        assert result == {(-100123, 42, "@1"), (-100456, None, "@2")}

    def test_display_name_set_on_bind(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1", window_name="france-chat")
        assert mgr.get_display_name("@1") == "france-chat"

    def test_multiple_topics_same_window(self, mgr: SessionManager) -> None:
        """Multiple topics can bind to the same window (e.g., General and a named topic)."""
        mgr.bind_topic(-100123, None, "@1")
        mgr.bind_topic(-100123, 42, "@1")
        assert mgr.get_window_for_topic(-100123, None) == "@1"
        assert mgr.get_window_for_topic(-100123, 42) == "@1"

    def test_different_groups_independent(self, mgr: SessionManager) -> None:
        mgr.bind_topic(-100123, 42, "@1")
        mgr.bind_topic(-100456, 42, "@2")
        assert mgr.get_window_for_topic(-100123, 42) == "@1"
        assert mgr.get_window_for_topic(-100456, 42) == "@2"


class TestResolveChatIdNegative:
    """Negative user_ids (group chat_ids) should pass through directly."""

    def test_negative_user_id_returned_directly(self, mgr: SessionManager) -> None:
        assert mgr.resolve_chat_id(-1003715707885, 42) == -1003715707885

    def test_negative_user_id_no_thread(self, mgr: SessionManager) -> None:
        assert mgr.resolve_chat_id(-1003715707885) == -1003715707885

    def test_positive_user_id_still_looks_up(self, mgr: SessionManager) -> None:
        mgr.set_group_chat_id(100, 1, -1001234567890)
        assert mgr.resolve_chat_id(100, 1) == -1001234567890


class TestTopicKey:
    def test_key_format(self) -> None:
        assert SessionManager._topic_key(-100123, 42) == "-100123:42"

    def test_key_general_topic(self) -> None:
        assert SessionManager._topic_key(-100123, None) == "-100123:0"
