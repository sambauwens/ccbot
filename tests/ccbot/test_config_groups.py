"""Tests for new group configuration — conversational groups, dev group, user roles."""

import pytest

from ccbot.config import Config


@pytest.fixture
def _base_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("ALLOWED_USERS", "100,200")
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))


@pytest.mark.usefixtures("_base_env")
class TestConversationalGroups:
    def test_parse_conversational_groups(self, monkeypatch):
        monkeypatch.setenv("CONVERSATIONAL_GROUPS", '{"france-2026": -100123}')
        cfg = Config()
        assert cfg.conversational_groups == {"france-2026": -100123}

    def test_is_conversational_group(self, monkeypatch):
        monkeypatch.setenv("CONVERSATIONAL_GROUPS", '{"france-2026": -100123}')
        cfg = Config()
        assert cfg.is_conversational_group(-100123) is True
        assert cfg.is_conversational_group(-999) is False

    def test_project_for_group_returns_project_name(self, monkeypatch):
        monkeypatch.setenv("CONVERSATIONAL_GROUPS", '{"france-2026": -100123}')
        cfg = Config()
        assert cfg.project_for_group(-100123) == "france-2026"

    def test_project_for_group_returns_none_for_dev_group(self, monkeypatch):
        monkeypatch.setenv("CONVERSATIONAL_GROUPS", '{"france-2026": -100123}')
        monkeypatch.setenv("DEV_GROUP", "-100456")
        cfg = Config()
        assert cfg.project_for_group(-100456) is None

    def test_empty_conversational_groups_default(self):
        cfg = Config()
        assert cfg.conversational_groups == {}


@pytest.mark.usefixtures("_base_env")
class TestDevGroup:
    def test_parse_dev_group(self, monkeypatch):
        monkeypatch.setenv("DEV_GROUP", "-100456")
        cfg = Config()
        assert cfg.dev_group == -100456

    def test_is_dev_group(self, monkeypatch):
        monkeypatch.setenv("DEV_GROUP", "-100456")
        cfg = Config()
        assert cfg.is_dev_group(-100456) is True
        assert cfg.is_dev_group(-999) is False

    def test_no_dev_group(self):
        cfg = Config()
        assert cfg.dev_group is None
        assert cfg.is_dev_group(-100456) is False


@pytest.mark.usefixtures("_base_env")
class TestDevUsers:
    def test_parse_dev_users(self, monkeypatch):
        monkeypatch.setenv("DEV_USERS", "100")
        cfg = Config()
        assert cfg.dev_users == {100}

    def test_is_dev_user(self, monkeypatch):
        monkeypatch.setenv("DEV_USERS", "100")
        cfg = Config()
        assert cfg.is_dev_user(100) is True
        assert cfg.is_dev_user(200) is False

    def test_empty_dev_users(self):
        cfg = Config()
        assert cfg.dev_users == set()

    def test_dev_group_user_topics_default(self):
        cfg = Config()
        assert cfg.dev_group_user_topics == "conversational"

    def test_dev_group_user_topics_custom(self, monkeypatch):
        monkeypatch.setenv("DEV_GROUP_USER_TOPICS", "dev")
        cfg = Config()
        assert cfg.dev_group_user_topics == "dev"


@pytest.mark.usefixtures("_base_env")
class TestBackwardCompat:
    def test_project_groups_includes_conversational(self, monkeypatch):
        """project_groups backward compat includes conversational groups."""
        monkeypatch.setenv("CONVERSATIONAL_GROUPS", '{"france-2026": -100123}')
        cfg = Config()
        assert cfg.project_groups == {"france-2026": -100123}

    def test_project_for_cwd_works_with_conversational(self, monkeypatch, tmp_path):
        projects_dir = tmp_path / "projects"
        france_dir = projects_dir / "france-2026"
        france_dir.mkdir(parents=True)
        monkeypatch.setenv("CCBOT_PROJECTS_DIR", str(projects_dir))
        monkeypatch.setenv("CONVERSATIONAL_GROUPS", '{"france-2026": -100123}')
        cfg = Config()
        assert cfg.project_for_cwd(str(france_dir)) == "france-2026"
        assert cfg.project_for_cwd(str(france_dir / "subdir")) == "france-2026"
        assert cfg.project_for_cwd("/other/path") is None
