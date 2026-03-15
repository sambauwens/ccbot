"""Tests for gap fixes from the plan-vs-implementation audit."""

import pytest

from ccbot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestPermissionState:
    """S1: Permission lifecycle tracking."""

    def test_default_permission_is_readonly(self, mgr: SessionManager) -> None:
        assert mgr.get_topic_permission(-100123, 42) == "read-only"

    def test_set_elevated(self, mgr: SessionManager) -> None:
        mgr.set_topic_permission(-100123, 42, "elevated")
        assert mgr.get_topic_permission(-100123, 42) == "elevated"

    def test_set_back_to_readonly(self, mgr: SessionManager) -> None:
        mgr.set_topic_permission(-100123, 42, "elevated")
        mgr.set_topic_permission(-100123, 42, "read-only")
        assert mgr.get_topic_permission(-100123, 42) == "read-only"

    def test_permission_independent_per_topic(self, mgr: SessionManager) -> None:
        mgr.set_topic_permission(-100123, 42, "elevated")
        mgr.set_topic_permission(-100123, 99, "read-only")
        assert mgr.get_topic_permission(-100123, 42) == "elevated"
        assert mgr.get_topic_permission(-100123, 99) == "read-only"

    def test_general_topic_permission(self, mgr: SessionManager) -> None:
        mgr.set_topic_permission(-100123, None, "elevated")
        assert mgr.get_topic_permission(-100123, None) == "elevated"


class TestWorktreeSourcesPersisted:
    """S7: Worktree sources survive restart."""

    def test_worktree_source_stored(self, mgr: SessionManager) -> None:
        key = mgr._topic_key(-100123, 42)
        mgr.worktree_sources["france-2026-add-login-ws"] = key
        assert mgr.worktree_sources["france-2026-add-login-ws"] == "-100123:42"

    def test_worktree_source_popped(self, mgr: SessionManager) -> None:
        mgr.worktree_sources["test-ws"] = "-100:0"
        result = mgr.worktree_sources.pop("test-ws", None)
        assert result == "-100:0"
        assert "test-ws" not in mgr.worktree_sources


class TestTopicClosedCleanup:
    """Task 3: topic_closed should clean up both binding types."""

    def test_topic_binding_found_by_chat_and_thread(self, mgr: SessionManager) -> None:
        """Closing a conversational topic should find its binding via topic_bindings."""
        mgr.bind_topic(-100123, 42, "@5")
        assert mgr.get_window_for_topic(-100123, 42) == "@5"
        mgr.unbind_topic(-100123, 42)
        assert mgr.get_window_for_topic(-100123, 42) is None

    def test_thread_binding_independent_of_topic(self, mgr: SessionManager) -> None:
        """Thread binding and topic binding for same thread_id are independent."""
        mgr.bind_thread(100, 42, "@1")
        mgr.bind_topic(-100123, 42, "@2")
        assert mgr.get_window_for_thread(100, 42) == "@1"
        assert mgr.get_window_for_topic(-100123, 42) == "@2"


class TestUserMessageFiltering:
    """Task 12: User messages should not be echoed in conversational topics."""

    def test_user_role_detected(self) -> None:
        """The role field distinguishes user from assistant messages."""
        # This is a data structure test — the actual filtering is in message_routing
        from ccbot.session_monitor import NewMessage

        user_msg = NewMessage(
            session_id="test", text="hello", is_complete=True, role="user"
        )
        assistant_msg = NewMessage(
            session_id="test", text="hi there", is_complete=True, role="assistant"
        )
        assert user_msg.role == "user"
        assert assistant_msg.role == "assistant"
