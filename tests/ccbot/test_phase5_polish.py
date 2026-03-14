"""Tests for Phase 5: Polish features."""

import pytest
from pathlib import Path

from ccbot.session import SessionManager


@pytest.fixture
def mgr(monkeypatch) -> SessionManager:
    monkeypatch.setattr(SessionManager, "_load_state", lambda self: None)
    monkeypatch.setattr(SessionManager, "_save_state", lambda self: None)
    return SessionManager()


class TestGitHubLinkGeneration:
    """GitHub links for referenced files in conversational topics."""

    def test_github_url_from_remote(self) -> None:
        """Parse GitHub URL from git remote."""
        from ccbot.bot import _parse_github_url

        # HTTPS remote
        assert (
            _parse_github_url("https://github.com/sambauwens/france-2026.git")
            == "https://github.com/sambauwens/france-2026"
        )
        # SSH remote
        assert (
            _parse_github_url("git@github.com:sambauwens/france-2026.git")
            == "https://github.com/sambauwens/france-2026"
        )
        # No .git suffix
        assert (
            _parse_github_url("https://github.com/sambauwens/france-2026")
            == "https://github.com/sambauwens/france-2026"
        )
        # Non-GitHub
        assert _parse_github_url("https://gitlab.com/foo/bar.git") is None

    def test_github_link_with_anchor(self) -> None:
        """Links should include header anchors when present."""
        from ccbot.bot import _make_github_link

        base = "https://github.com/sambauwens/france-2026"
        assert (
            _make_github_link(base, "docs/plan.md", "main", "Architecture")
            == "https://github.com/sambauwens/france-2026/blob/main/docs/plan.md#architecture"
        )
        # No heading
        assert (
            _make_github_link(base, "docs/plan.md", "main")
            == "https://github.com/sambauwens/france-2026/blob/main/docs/plan.md"
        )


class TestReminderRoutingToGeneral:
    """Reminders should be sent to General topic (thread_id=None)."""

    def test_general_topic_key(self) -> None:
        """General topic uses thread_id=0 in keys."""
        assert SessionManager._topic_key(-100123, None) == "-100123:0"


class TestRetrospectiveDoc:
    """docs/retrospective.md should exist and be linked from README."""

    def test_retrospective_doc_exists(self) -> None:
        doc = Path(__file__).parents[2] / "docs" / "retrospective.md"
        assert doc.exists(), "docs/retrospective.md should exist"

    def test_readme_links_retrospective(self) -> None:
        readme = Path(__file__).parents[2] / "README.md"
        content = readme.read_text()
        assert "retrospective" in content.lower()
