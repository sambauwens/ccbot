"""Tmux session management via libtmux — session-per-instance model.

Each Claude Code instance gets its own tmux session (not a window inside
a shared session). This makes sessions discoverable by `dev go` and
attachable from the terminal.

Window IDs (@0, @12, etc.) remain globally unique across all tmux sessions
and are used as the internal routing key throughout the codebase.

All blocking libtmux calls are wrapped in asyncio.to_thread().

Key class: TmuxManager (singleton instantiated as `tmux_manager`).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import libtmux

from .config import SENSITIVE_ENV_VARS

# Validate session IDs are UUIDs (prevent command injection via --resume)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

logger = logging.getLogger(__name__)


@dataclass
class TmuxWindow:
    """Information about a tmux window."""

    window_id: str
    window_name: str
    cwd: str  # Current working directory
    pane_current_command: str = ""  # Process running in active pane
    session_name: str = ""  # Name of the parent tmux session


class TmuxManager:
    """Manages tmux sessions for Claude Code instances.

    Each Claude instance runs in its own tmux session. Window IDs are
    globally unique and used as the primary routing key.
    """

    def __init__(self) -> None:
        self._server: libtmux.Server | None = None

    @property
    def server(self) -> libtmux.Server:
        """Get or create tmux server connection."""
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def _find_window(self, window_id: str) -> libtmux.Window | None:
        """Find a window by ID across all tmux sessions."""
        try:
            return self.server.windows.get(window_id=window_id)
        except Exception:
            return None

    @staticmethod
    def _scrub_session_env(session: libtmux.Session) -> None:
        """Remove sensitive env vars from a tmux session environment."""
        for var in SENSITIVE_ENV_VARS:
            try:
                session.unset_environment(var)
            except Exception:
                pass

    async def list_windows(self) -> list[TmuxWindow]:
        """List all windows across all tmux sessions.

        Returns:
            List of TmuxWindow with window info and cwd
        """

        def _sync_list() -> list[TmuxWindow]:
            windows = []
            try:
                sessions = self.server.sessions
            except Exception:
                return windows

            for session in sessions:
                sess_name = session.session_name or ""
                for window in session.windows:
                    name = window.window_name or ""
                    try:
                        pane = window.active_pane
                        if pane:
                            cwd = pane.pane_current_path or ""
                            pane_cmd = pane.pane_current_command or ""
                        else:
                            cwd = ""
                            pane_cmd = ""

                        windows.append(
                            TmuxWindow(
                                window_id=window.window_id or "",
                                window_name=name,
                                cwd=cwd,
                                pane_current_command=pane_cmd,
                                session_name=sess_name,
                            )
                        )
                    except Exception as e:
                        logger.debug("Error getting window info: %s", e)

            return windows

        return await asyncio.to_thread(_sync_list)

    async def find_window_by_name(self, window_name: str) -> TmuxWindow | None:
        """Find a window by its name (searches all sessions)."""
        windows = await self.list_windows()
        for window in windows:
            if window.window_name == window_name:
                return window
        return None

    async def find_window_by_id(self, window_id: str) -> TmuxWindow | None:
        """Find a window by its tmux window ID (e.g. '@0', '@12')."""
        windows = await self.list_windows()
        for window in windows:
            if window.window_id == window_id:
                return window
        return None

    async def capture_pane(self, window_id: str, with_ansi: bool = False) -> str | None:
        """Capture the visible text content of a window's active pane."""
        if with_ansi:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "tmux",
                    "capture-pane",
                    "-e",
                    "-p",
                    "-t",
                    window_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    return stdout.decode("utf-8")
                logger.error(
                    "Failed to capture pane %s: %s", window_id, stderr.decode("utf-8")
                )
                return None
            except Exception as e:
                logger.error("Unexpected error capturing pane %s: %s", window_id, e)
                return None

        def _sync_capture() -> str | None:
            window = self._find_window(window_id)
            if not window:
                return None
            try:
                pane = window.active_pane
                if not pane:
                    return None
                lines = pane.capture_pane()
                return "\n".join(lines) if isinstance(lines, list) else str(lines)
            except Exception as e:
                logger.error("Failed to capture pane %s: %s", window_id, e)
                return None

        return await asyncio.to_thread(_sync_capture)

    async def send_keys(
        self, window_id: str, text: str, enter: bool = True, literal: bool = True
    ) -> bool:
        """Send keys to a specific window."""
        if literal and enter:

            def _send_literal(chars: str) -> bool:
                window = self._find_window(window_id)
                if not window:
                    logger.error("Window %s not found", window_id)
                    return False
                try:
                    pane = window.active_pane
                    if not pane:
                        logger.error("No active pane in window %s", window_id)
                        return False
                    pane.send_keys(chars, enter=False, literal=True)
                    return True
                except Exception as e:
                    logger.error("Failed to send keys to window %s: %s", window_id, e)
                    return False

            def _send_enter() -> bool:
                window = self._find_window(window_id)
                if not window:
                    return False
                try:
                    pane = window.active_pane
                    if not pane:
                        return False
                    pane.send_keys("", enter=True, literal=False)
                    return True
                except Exception as e:
                    logger.error("Failed to send Enter to window %s: %s", window_id, e)
                    return False

            # Claude Code's ! command mode
            if text.startswith("!"):
                if not await asyncio.to_thread(_send_literal, "!"):
                    return False
                rest = text[1:]
                if rest:
                    await asyncio.sleep(1.0)
                    if not await asyncio.to_thread(_send_literal, rest):
                        return False
            else:
                if not await asyncio.to_thread(_send_literal, text):
                    return False
            await asyncio.sleep(0.5)
            return await asyncio.to_thread(_send_enter)

        # Special keys (literal=False) or no-enter
        def _sync_send_keys() -> bool:
            window = self._find_window(window_id)
            if not window:
                logger.error("Window %s not found", window_id)
                return False
            try:
                pane = window.active_pane
                if not pane:
                    logger.error("No active pane in window %s", window_id)
                    return False
                pane.send_keys(text, enter=enter, literal=literal)
                return True
            except Exception as e:
                logger.error("Failed to send keys to window %s: %s", window_id, e)
                return False

        return await asyncio.to_thread(_sync_send_keys)

    async def rename_window(self, window_id: str, new_name: str) -> bool:
        """Rename a tmux window by its ID."""

        def _sync_rename() -> bool:
            window = self._find_window(window_id)
            if not window:
                return False
            try:
                window.rename_window(new_name)
                logger.info("Renamed window %s to '%s'", window_id, new_name)
                return True
            except Exception as e:
                logger.error("Failed to rename window %s: %s", window_id, e)
                return False

        return await asyncio.to_thread(_sync_rename)

    async def kill_window(self, window_id: str) -> bool:
        """Kill the tmux session containing this window.

        In the session-per-instance model, each session has one window,
        so killing the session is equivalent to killing the window.
        """

        def _sync_kill() -> bool:
            window = self._find_window(window_id)
            if not window:
                return False
            try:
                session = window.session
                if session:
                    session.kill()
                    logger.info(
                        "Killed session '%s' (window %s)",
                        session.session_name,
                        window_id,
                    )
                else:
                    window.kill()
                    logger.info("Killed window %s (no parent session)", window_id)
                return True
            except Exception as e:
                logger.error("Failed to kill window %s: %s", window_id, e)
                return False

        return await asyncio.to_thread(_sync_kill)

    async def create_window(
        self,
        work_dir: str,
        window_name: str | None = None,
        start_claude: bool = True,
        resume_session_id: str | None = None,
    ) -> tuple[bool, str, str, str]:
        """Create a new tmux session and optionally start Claude Code.

        Each Claude instance gets its own tmux session, making it
        discoverable by `dev go` and attachable from the terminal.

        Args:
            work_dir: Working directory for the new session
            window_name: Session/window name (defaults to directory name)
            start_claude: Whether to start claude command
            resume_session_id: If set, append --resume <id> to claude command

        Returns:
            Tuple of (success, message, session_name, window_id)
        """
        # Validate resume_session_id is a UUID to prevent command injection
        if resume_session_id and not _UUID_RE.match(resume_session_id):
            return False, "Invalid session ID format", "", ""

        path = Path(work_dir).expanduser().resolve()
        if not path.exists():
            return False, f"Directory does not exist: {work_dir}", "", ""
        if not path.is_dir():
            return False, f"Not a directory: {work_dir}", "", ""

        session_name = window_name if window_name else path.name

        # Deduplicate session name
        base_name = session_name
        counter = 2

        def _session_exists(name: str) -> bool:
            try:
                return self.server.sessions.get(session_name=name) is not None
            except Exception:
                return False

        while await asyncio.to_thread(_session_exists, session_name):
            session_name = f"{base_name}-{counter}"
            counter += 1

        def _create_and_start() -> tuple[bool, str, str, str]:
            try:
                # Handle TCC-protected paths (iCloud)
                if "/Library/Mobile Documents/" in str(path):
                    session = self.server.new_session(
                        session_name=session_name,
                    )
                    pane = session.active_pane
                    if pane:
                        pane.send_keys(f"cd {str(path)!r}", enter=True)
                else:
                    session = self.server.new_session(
                        session_name=session_name,
                        start_directory=str(path),
                    )

                self._scrub_session_env(session)

                window = session.active_window
                if not window:
                    return False, "Failed to get window", "", ""

                wid = window.window_id or ""

                # Prevent Claude Code from overriding window name
                window.set_window_option("allow-rename", "off")

                if start_claude:
                    pane = window.active_pane
                    if pane:
                        from .config import config

                        cmd = config.claude_command
                        if resume_session_id:
                            cmd = f"{cmd} --resume {resume_session_id}"
                        pane.send_keys(cmd, enter=True)

                logger.info(
                    "Created session '%s' (window_id=%s) at %s",
                    session_name,
                    wid,
                    path,
                )
                return (
                    True,
                    f"Created session '{session_name}' at {path}",
                    session_name,
                    wid,
                )

            except Exception as e:
                logger.error("Failed to create session: %s", e)
                return False, f"Failed to create session: {e}", "", ""

        return await asyncio.to_thread(_create_and_start)


# Global instance
tmux_manager = TmuxManager()
