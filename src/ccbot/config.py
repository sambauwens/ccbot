"""Application configuration — reads env vars and exposes a singleton.

Loads TELEGRAM_BOT_TOKEN, ALLOWED_USERS, tmux/Claude paths, project
group mappings, and monitoring intervals from environment variables
(with .env support).
.env loading priority: local .env (cwd) > $CCBOT_DIR/.env (default ~/.ccbot).
The module-level `config` instance is imported by nearly every other module.

Key class: Config (singleton instantiated as `config`).
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .utils import ccbot_dir

logger = logging.getLogger(__name__)

# Env vars that must not leak to child processes (e.g. Claude Code via tmux)
SENSITIVE_ENV_VARS = {"TELEGRAM_BOT_TOKEN", "ALLOWED_USERS", "OPENAI_API_KEY"}


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.config_dir = ccbot_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Load .env: local (cwd) takes priority over config_dir
        # load_dotenv default override=False means first-loaded wins
        local_env = Path(".env")
        global_env = self.config_dir / ".env"
        if local_env.is_file():
            load_dotenv(local_env)
            logger.debug("Loaded env from %s", local_env.resolve())
        if global_env.is_file():
            load_dotenv(global_env)
            logger.debug("Loaded env from %s", global_env)

        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN") or ""
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

        allowed_users_str = os.getenv("ALLOWED_USERS", "")
        if not allowed_users_str:
            raise ValueError("ALLOWED_USERS environment variable is required")
        try:
            self.allowed_users: set[int] = {
                int(uid.strip()) for uid in allowed_users_str.split(",") if uid.strip()
            }
        except ValueError as e:
            raise ValueError(
                f"ALLOWED_USERS contains non-numeric value: {e}. "
                "Expected comma-separated Telegram user IDs."
            ) from e

        # Claude command to run in new windows
        self.claude_command = os.getenv("CLAUDE_COMMAND", "claude")

        # All state files live under config_dir
        self.state_file = self.config_dir / "state.json"
        self.session_map_file = self.config_dir / "session_map.json"
        self.monitor_state_file = self.config_dir / "monitor_state.json"

        # --- Group routing ---
        # PROJECTS_DIR: base directory for projects (default ~/dev/@active)
        self.projects_dir = Path(
            os.getenv("CCBOT_PROJECTS_DIR", str(Path.home() / "dev" / "@active"))
        )

        # CONVERSATIONAL_GROUPS: project_name → chat_id (per-project, multi-user)
        conv_json = os.getenv("CONVERSATIONAL_GROUPS", "{}")
        try:
            raw_conv = json.loads(conv_json)
            self.conversational_groups: dict[str, int] = {
                str(k): int(v) for k, v in raw_conv.items()
            }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse CONVERSATIONAL_GROUPS: %s", e)
            self.conversational_groups = {}

        # Reverse: chat_id → project name (conversational groups only)
        self._conv_group_to_project: dict[int, str] = {
            v: k for k, v in self.conversational_groups.items()
        }

        # DEV_GROUP: single chat_id for the dev group (all projects, Sam only)
        dev_group_str = os.getenv("DEV_GROUP", "")
        self.dev_group: int | None = int(dev_group_str) if dev_group_str else None

        # DEV_USERS: comma-separated user IDs who can use dev group and $accept
        dev_users_str = os.getenv("DEV_USERS", "")
        self.dev_users: set[int] = (
            {int(uid.strip()) for uid in dev_users_str.split(",") if uid.strip()}
            if dev_users_str
            else set()
        )

        # Whether user-created topics in dev group are conversational
        self.dev_group_user_topics = os.getenv(
            "DEV_GROUP_USER_TOPICS", "conversational"
        )

        # Backward compat: combined project→group mapping for project_for_cwd
        self.project_groups: dict[str, int] = dict(self.conversational_groups)

        # Claude Code session monitoring configuration
        # Support custom projects path for Claude variants (e.g., cc-mirror, zai)
        # Priority: CCBOT_CLAUDE_PROJECTS_PATH > CLAUDE_CONFIG_DIR/projects > default
        custom_projects_path = os.getenv("CCBOT_CLAUDE_PROJECTS_PATH")
        claude_config_dir = os.getenv("CLAUDE_CONFIG_DIR")

        if custom_projects_path:
            self.claude_projects_path = Path(custom_projects_path)
        elif claude_config_dir:
            self.claude_projects_path = Path(claude_config_dir) / "projects"
        else:
            self.claude_projects_path = Path.home() / ".claude" / "projects"

        self.monitor_poll_interval = float(os.getenv("MONITOR_POLL_INTERVAL", "2.0"))

        # Reminder monitoring interval in seconds (default: 6 hours)
        self.reminder_interval = float(os.getenv("REMINDER_INTERVAL", str(6 * 3600)))

        # Display user messages in history and real-time notifications
        # When True, user messages are shown with a 👤 prefix
        self.show_user_messages = True

        # Show thinking blocks (∴ Thinking...) in Telegram
        self.show_thinking = os.getenv("CCBOT_SHOW_THINKING", "").lower() == "true"
        # Show tool_use/tool_result messages (Read, Write, Bash, etc.)
        self.show_tool_messages = os.getenv("CCBOT_SHOW_TOOLS", "").lower() == "true"

        # Show hidden (dot) directories in directory browser
        self.show_hidden_dirs = (
            os.getenv("CCBOT_SHOW_HIDDEN_DIRS", "").lower() == "true"
        )

        # OpenAI API for voice message transcription (optional)
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_base_url: str = os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )

        # Scrub sensitive vars from os.environ so child processes never inherit them.
        # Values are already captured in Config attributes above.
        for var in SENSITIVE_ENV_VARS:
            os.environ.pop(var, None)

        logger.debug(
            "Config initialized: dir=%s, token=%s..., allowed_users=%d, "
            "conversational_groups=%s, dev_group=%s, dev_users=%s, "
            "claude_projects_path=%s",
            self.config_dir,
            self.telegram_bot_token[:8],
            len(self.allowed_users),
            list(self.conversational_groups.keys()),
            self.dev_group,
            self.dev_users,
            self.claude_projects_path,
        )

    def is_user_allowed(self, user_id: int) -> bool:
        """Check if a user is in the allowed list."""
        return user_id in self.allowed_users

    def project_dir(self, project_name: str) -> Path:
        """Resolve the directory for a project by convention."""
        return self.projects_dir / project_name

    def project_for_group(self, chat_id: int) -> str | None:
        """Look up the project name for a group chat_id (conversational groups)."""
        return self._conv_group_to_project.get(chat_id)

    def is_conversational_group(self, chat_id: int) -> bool:
        """Check if a chat_id is a conversational group."""
        return chat_id in self._conv_group_to_project

    def is_dev_group(self, chat_id: int) -> bool:
        """Check if a chat_id is the dev group."""
        return self.dev_group is not None and chat_id == self.dev_group

    def is_dev_user(self, user_id: int) -> bool:
        """Check if a user can use the dev group and $accept plans."""
        return user_id in self.dev_users

    def project_for_cwd(self, cwd: str) -> str | None:
        """Look up the project name for a cwd path.

        Matches if cwd is at or under projects_dir/<project_name>.
        """
        try:
            cwd_path = Path(cwd).resolve()
        except (OSError, ValueError):
            return None
        for project_name in self.project_groups:
            project_path = self.project_dir(project_name).resolve()
            if cwd_path == project_path or project_path in cwd_path.parents:
                return project_name
        return None


config = Config()
