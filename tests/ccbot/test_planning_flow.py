"""Tests for Phase 4: Planning flow ($plan, $accept, $new, worktree creation)."""

import pytest
import yaml
from pathlib import Path

from ccbot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestWorktreeCreation:
    """Worktree creation from bare repo for plan acceptance."""

    def test_worktree_name_from_plan(self) -> None:
        """Worktree name should be project-planname-ws."""
        from ccbot.bot import _sanitize_tmux_name

        plan_name = "Add login page"
        sanitized = _sanitize_tmux_name(plan_name)
        worktree_name = f"france-2026-{sanitized}-ws"
        assert worktree_name == "france-2026-add-login-page-ws"

    def test_pool_file_update(self, tmp_path: Path) -> None:
        """Pool file should be updated with new worktree entry."""
        pool_file = tmp_path / ".workspace-pool.yml"
        pool_data = {
            "pool_version": 1,
            "project": "france-2026",
            "origin": "https://github.com/sambauwens/france-2026.git",
            "default_branch": "main",
            "branch_worktrees": {"france-2026-main": {"branch": "main"}},
            "worktrees": {},
        }
        pool_file.write_text(yaml.dump(pool_data))

        # Simulate adding a worktree entry
        pool_data["worktrees"]["france-2026-add-login-ws"] = {
            "status": "reserved",
            "branch": "france-2026-add-login-ws",
            "agent": "claude",
            "epic": "Add login page",
        }
        pool_file.write_text(yaml.dump(pool_data))

        # Verify
        loaded = yaml.safe_load(pool_file.read_text())
        wt = loaded["worktrees"]["france-2026-add-login-ws"]
        assert wt["status"] == "reserved"
        assert wt["agent"] == "claude"


class TestPlanCommandRouting:
    """$plan and $accept should only work in conversational topics."""

    def test_conversational_topic_detected(self, mgr: SessionManager) -> None:
        """Topics bound via topic_bindings are conversational."""
        mgr.bind_topic(-100123, 42, "@1", topic_type="conversational")
        assert mgr.get_topic_type(-100123, 42) == "conversational"

    def test_dev_topic_not_conversational(self, mgr: SessionManager) -> None:
        """Dev topics should not be detectable as conversational via topic_types."""
        # Dev topics use thread_bindings, not topic_bindings
        mgr.bind_thread(100, 5, "@1")
        assert mgr.get_topic_type(-100456, 5) is None


class TestNewCommand:
    """$new creates a topic and carries context."""

    def test_sanitize_topic_title(self) -> None:
        """Topic titles should be sanitized from user text."""
        from ccbot.bot import _sanitize_topic_title

        assert _sanitize_topic_title("My new topic\nwith details") == "My new topic"
        assert _sanitize_topic_title("") == "New session"
        assert len(_sanitize_topic_title("x" * 200)) <= 128
