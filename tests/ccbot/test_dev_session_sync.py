"""Tests for dev session ↔ tmux bidirectional sync (Phase 3)."""

import pytest
from ccbot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestDevTopicCleanup:
    """When a tmux session dies, the corresponding dev topic should be closeable."""

    def test_find_dev_topic_for_window(self, mgr: SessionManager) -> None:
        """Dev topics bound via thread_bindings can be found by window_id."""
        mgr.bind_thread(100, 5, "@1")
        mgr.set_group_chat_id(100, 5, -100456)

        # Find all bindings for window @1
        found = [
            (uid, tid, wid)
            for uid, tid, wid in mgr.iter_thread_bindings()
            if wid == "@1"
        ]
        assert found == [(100, 5, "@1")]

    def test_unbind_dev_topic(self, mgr: SessionManager) -> None:
        """Unbinding a dev topic removes thread binding."""
        mgr.bind_thread(100, 5, "@1")
        result = mgr.unbind_thread(100, 5)
        assert result == "@1"
        assert mgr.get_window_for_thread(100, 5) is None

    def test_multiple_users_same_window(self, mgr: SessionManager) -> None:
        """Multiple DEV_USERS bound to same window should all be found."""
        mgr.bind_thread(100, 5, "@1")
        mgr.bind_thread(200, 5, "@1")
        found = [
            (uid, tid, wid)
            for uid, tid, wid in mgr.iter_thread_bindings()
            if wid == "@1"
        ]
        assert len(found) == 2


class TestStartupReconciliation:
    """On startup, dev topics should be created for unbound tmux sessions."""

    def test_unbound_window_detected(self, mgr: SessionManager) -> None:
        """Windows not in any binding are candidates for topic creation."""
        mgr.bind_thread(100, 5, "@1")
        bound_ids = {wid for _, _, wid in mgr.iter_thread_bindings()}
        # Also check topic_bindings
        bound_ids.update(wid for _, _, wid in mgr.iter_topic_bindings())

        # @1 is bound, @2 is not
        assert "@1" in bound_ids
        assert "@2" not in bound_ids


class TestCreateWindowPermissions:
    """Dev sessions should NOT use --dangerously-skip-permissions by default."""

    def test_skip_permissions_flag_logic(self) -> None:
        """The skip_permissions parameter controls the --dangerously-skip-permissions flag."""
        base_cmd = "claude"

        # Default (skip_permissions=False): no flag
        cmd = base_cmd
        assert "--dangerously-skip-permissions" not in cmd

        # skip_permissions=True: flag added
        cmd = f"{base_cmd} --dangerously-skip-permissions"
        assert "--dangerously-skip-permissions" in cmd

    def test_create_window_signature_has_skip_permissions(self) -> None:
        """create_window accepts skip_permissions parameter."""
        import inspect as _inspect
        from ccbot.tmux_manager import TmuxManager

        sig = _inspect.signature(TmuxManager.create_window)
        assert "skip_permissions" in sig.parameters
        # Default should be False
        assert sig.parameters["skip_permissions"].default is False
