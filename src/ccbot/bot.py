"""Telegram bot orchestrator -- wires handlers and manages bot lifecycle.

Thin orchestrator that registers all command/callback/message handlers,
manages shared state, and controls the bot lifecycle (post_init, post_shutdown).

Handler modules (in handlers/):
  - conversational: Conversational topic handling ($plan, $accept, $new, $merge)
  - dev_session_sync: Dev group session sync (auto-create/close topics)
  - session_commands: Session commands (/start, /history, /screenshot, etc.)
  - topic_lifecycle: Topic lifecycle events (created, closed, edited)
  - media: Photo, voice, unsupported content, forward command handlers
  - message_routing: Text message routing and outbound message delivery
  - callback_data: Callback data constants
  - message_queue: Per-user message queue management
  - message_sender: Safe message sending helpers
  - directory_browser: Directory browser UI
  - interactive_ui: Interactive UI handling
  - status_polling: Terminal status polling
  - response_builder: Response message building

Key functions: create_bot().
"""

import asyncio
import io
import logging
import re
from pathlib import Path

from telegram import (
    BotCommand,
    InputMediaDocument,
    Update,
)
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import config
from .handlers.callback_data import (
    CB_ASK_DOWN,
    CB_ASK_ENTER,
    CB_ASK_ESC,
    CB_ASK_LEFT,
    CB_ASK_KEYS,
    CB_ASK_REFRESH,
    CB_ASK_RIGHT,
    CB_ASK_SPACE,
    CB_ASK_TAB,
    CB_ASK_UP,
    CB_DIR_CANCEL,
    CB_DIR_CONFIRM,
    CB_DIR_PAGE,
    CB_DIR_SELECT,
    CB_DIR_UP,
    CB_HISTORY_NEXT,
    CB_HISTORY_PREV,
    CB_SESSION_CANCEL,
    CB_SESSION_NEW,
    CB_SESSION_SELECT,
    CB_KEYS_PREFIX,
    CB_SCREENSHOT_REFRESH,
    CB_WIN_BIND,
    CB_WIN_CANCEL,
    CB_WIN_NEW,
)
from .handlers.directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    SESSIONS_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_SESSION,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    build_session_picker,
    clear_browse_state,
    clear_session_picker_state,
    clear_window_picker_state,
)
from .handlers.history import send_history
from .handlers.interactive_ui import (
    clear_interactive_msg,
    handle_interactive_ui,
)
from .handlers.message_queue import shutdown_workers
from .handlers.message_sender import safe_edit, safe_send
from .handlers.status_polling import status_poll_loop
from .screenshot import text_to_image
from .session import session_manager
from .session_monitor import NewMessage, NewSession, SessionMonitor
from .tmux_manager import tmux_manager
from .reminder_monitor import handle_reminder_callback, start_reminder_loop
from .transcribe import close_client as close_transcribe_client

# --- Handler module imports ---
from .handlers.session_commands import (
    CC_COMMANDS,
    start_command,
    history_command,
    screenshot_command,
    esc_command,
    usage_command,
    bind_command,
    unbind_command,
    mode_command,
    _build_screenshot_keyboard,
    _KEYS_SEND_MAP,
    _KEY_LABELS,
)
from .handlers.topic_lifecycle import (
    topic_closed_handler,
    topic_created_handler,
    topic_edited_handler,
)
from .handlers.media import (
    photo_handler,
    voice_handler,
    unsupported_content_handler,
    forward_command_handler,
)
from .handlers.message_routing import (
    text_handler,
    handle_new_message,
)
from .handlers.dev_session_sync import (
    handle_new_session,
    reconcile_dev_topics,
    handle_session_removed,
)

# Re-exports for backward compatibility (tests import from ccbot.bot)
from .handlers.conversational import _parse_github_url as _parse_github_url  # noqa: F401
from .handlers.conversational import _make_github_link as _make_github_link  # noqa: F401

logger = logging.getLogger(__name__)

# --- Shared state (used across handler modules via `from ..bot import`) ---

# Validate tmux window IDs from callback data (prevent injection)
_WINDOW_ID_RE = re.compile(r"^@\d+$")


def _is_valid_window_id(window_id: str) -> bool:
    """Check that a window ID has the expected @<digits> format."""
    return bool(_WINDOW_ID_RE.match(window_id))


# Topic name cache: (chat_id, thread_id) -> topic name
# Populated by FORUM_TOPIC_CREATED status messages.
_topic_names: dict[tuple[int, int], str] = {}


def _sanitize_tmux_name(title: str) -> str:
    """Make a user title tmux-friendly: lowercase, dashes, no special chars."""
    name = title.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    return name[:60] or "session"


def _sanitize_topic_title(text: str) -> str:
    """Extract a topic title from user text: first line, max 128 chars."""
    first_line = text.split("\n", 1)[0].strip()
    if not first_line:
        return "New session"
    return first_line[:128]


# Session monitor instance
session_monitor: SessionMonitor | None = None

# Track which conversational topic spawned which worktree (for merge reminders)
# worktree_name -> (source_chat_id, source_thread_id)
_worktree_sources: dict[str, tuple[int, int | None]] = {}

# Status polling task
_status_poll_task: asyncio.Task | None = None

# Reminder monitor task
_reminder_task: asyncio.Task | None = None


def is_user_allowed(user_id: int | None) -> bool:
    return user_id is not None and config.is_user_allowed(user_id)


def _get_thread_id(update: Update) -> int | None:
    """Extract thread_id from an update, returning None if not in a named topic."""
    msg = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if msg is None:
        return None
    tid = getattr(msg, "message_thread_id", None)
    if tid is None or tid == 1:
        return None
    return tid


# --- Window creation helper (used by callback_handler) ---


async def _create_and_bind_window(
    query: object,
    context: ContextTypes.DEFAULT_TYPE,
    user: object,
    selected_path: str,
    pending_thread_id: int | None,
    resume_session_id: str | None = None,
) -> None:
    """Create a tmux window, bind it to a topic, and forward pending text.

    Shared by CB_DIR_CONFIRM (no sessions), CB_SESSION_NEW, and CB_SESSION_SELECT.
    """
    from telegram import CallbackQuery, User

    assert isinstance(query, CallbackQuery)
    assert isinstance(user, User)

    success, message, created_wname, created_wid = await tmux_manager.create_window(
        selected_path, resume_session_id=resume_session_id
    )
    if success:
        logger.info(
            "Window created: %s (id=%s) at %s (user=%d, thread=%s, resume=%s)",
            created_wname,
            created_wid,
            selected_path,
            user.id,
            pending_thread_id,
            resume_session_id,
        )
        # Wait for Claude Code's SessionStart hook to register in session_map.
        # Resume sessions take longer to start (loading session state), so use
        # a longer timeout to avoid silently dropping messages.
        hook_timeout = 15.0 if resume_session_id else 5.0
        hook_ok = await session_manager.wait_for_session_map_entry(
            created_wid, timeout=hook_timeout
        )

        # --resume creates a new session_id in the hook, but messages continue
        # writing to the resumed session's JSONL file. Override window_state to
        # track the original session_id so the monitor can route messages back.
        if resume_session_id:
            ws = session_manager.get_window_state(created_wid)
            if not hook_ok:
                # Hook timed out -- manually populate window_state so the
                # monitor can still route messages back to this topic.
                logger.warning(
                    "Hook timed out for resume window %s, "
                    "manually setting session_id=%s cwd=%s",
                    created_wid,
                    resume_session_id,
                    selected_path,
                )
                ws.session_id = resume_session_id
                ws.cwd = str(selected_path)
                ws.window_name = created_wname
                session_manager._save_state()
            elif ws.session_id != resume_session_id:
                logger.info(
                    "Resume override: window %s session_id %s -> %s",
                    created_wid,
                    ws.session_id,
                    resume_session_id,
                )
                ws.session_id = resume_session_id
                session_manager._save_state()

        if pending_thread_id is not None:
            # Thread bind flow: bind thread to newly created window
            session_manager.bind_thread(
                user.id, pending_thread_id, created_wid, window_name=created_wname
            )

            # Do NOT rename the Telegram topic -- preserve user's chosen name
            resolved_chat = session_manager.resolve_chat_id(user.id, pending_thread_id)

            status = "Resumed" if resume_session_id else "Created"
            await safe_edit(
                query,
                f"✅ {message}\n\n{status}. Send messages here.",
            )

            # Send pending text if any
            pending_text = (
                context.user_data.get("_pending_thread_text")
                if context.user_data
                else None
            )
            if pending_text:
                logger.debug(
                    "Forwarding pending text to window %s (len=%d)",
                    created_wname,
                    len(pending_text),
                )
                if context.user_data is not None:
                    context.user_data.pop("_pending_thread_text", None)
                    context.user_data.pop("_pending_thread_id", None)
                send_ok, send_msg = await session_manager.send_to_window(
                    created_wid,
                    pending_text,
                )
                if not send_ok:
                    logger.warning("Failed to forward pending text: %s", send_msg)
                    await safe_send(
                        context.bot,
                        resolved_chat,
                        f"❌ Failed to send pending message: {send_msg}",
                        message_thread_id=pending_thread_id,
                    )
            elif context.user_data is not None:
                context.user_data.pop("_pending_thread_id", None)
        else:
            # Should not happen in topic-only mode, but handle gracefully
            await safe_edit(query, f"✅ {message}")
    else:
        await safe_edit(query, f"❌ {message}")
        if pending_thread_id is not None and context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
    await query.answer("Created" if success else "Failed")


# --- Callback query handler ---


def _resolve_interactive_user_id(user_id: int, thread_id: int | None, chat_id: int | None) -> int:
    """Resolve the correct user_id for interactive UI state.

    For conversational topics (topic_bindings), the interactive state is keyed
    by chat_id (negative), not by user.id. Check if there's a topic_binding
    for this chat and return the chat_id if so.
    """
    if chat_id and session_manager.get_window_for_topic(chat_id, thread_id):
        return chat_id
    return user_id


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        await query.answer("Not authorized")
        return

    data = query.data

    # Handle reminder callbacks first (rem:+1d:..., rem:done:...)
    if data.startswith("rem:"):
        handled = await handle_reminder_callback(context.bot, data, query)
        if handled:
            return

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove -- see session.py docs.
    cb_thread_id = _get_thread_id(update)
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, cb_thread_id, chat.id)

    # History: older/newer pagination
    # Format: hp:<page>:<window_id>:<start>:<end> or hn:<page>:<window_id>:<start>:<end>
    if data.startswith(CB_HISTORY_PREV) or data.startswith(CB_HISTORY_NEXT):
        prefix_len = len(CB_HISTORY_PREV)  # same length for both
        rest = data[prefix_len:]
        try:
            parts = rest.split(":")
            if len(parts) < 4:
                # Old format without byte range: page:window_id
                offset_str, window_id = rest.split(":", 1)
                start_byte, end_byte = 0, 0
            else:
                # New format: page:window_id:start:end (window_id may contain colons)
                offset_str = parts[0]
                start_byte = int(parts[-2])
                end_byte = int(parts[-1])
                window_id = ":".join(parts[1:-2])
            offset = int(offset_str)
        except (ValueError, IndexError):
            await query.answer("Invalid data")
            return

        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await send_history(
                query,
                window_id,
                offset=offset,
                edit=True,
                start_byte=start_byte,
                end_byte=end_byte,
            )
        else:
            await safe_edit(query, "Window no longer exists.")
        await query.answer("Page updated")

    # Directory browser handlers
    elif data.startswith(CB_DIR_SELECT):
        # Validate: callback must come from the same topic that started browsing
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        # callback_data contains index, not dir name (to avoid 64-byte limit)
        try:
            idx = int(data[len(CB_DIR_SELECT) :])
        except ValueError:
            await query.answer("Invalid data")
            return

        # Look up dir name from cached subdirs
        cached_dirs: list[str] = (
            context.user_data.get(BROWSE_DIRS_KEY, []) if context.user_data else []
        )
        if idx < 0 or idx >= len(cached_dirs):
            await query.answer(
                "Directory list changed, please refresh", show_alert=True
            )
            return
        subdir_name = cached_dirs[idx]

        default_path = str(Path.cwd())
        current_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
        new_path = (Path(current_path) / subdir_name).resolve()

        if not new_path.exists() or not new_path.is_dir():
            await query.answer("Directory not found", show_alert=True)
            return

        new_path_str = str(new_path)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = new_path_str
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard, subdirs = build_directory_browser(new_path_str)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_UP:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        default_path = str(Path.cwd())
        current_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
        current = Path(current_path).resolve()
        parent = current.parent
        # No restriction - allow navigating anywhere

        parent_path = str(parent)
        if context.user_data is not None:
            context.user_data[BROWSE_PATH_KEY] = parent_path
            context.user_data[BROWSE_PAGE_KEY] = 0

        msg_text, keyboard, subdirs = build_directory_browser(parent_path)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data.startswith(CB_DIR_PAGE):
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        try:
            pg = int(data[len(CB_DIR_PAGE) :])
        except ValueError:
            await query.answer("Invalid data")
            return
        default_path = str(Path.cwd())
        current_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
        if context.user_data is not None:
            context.user_data[BROWSE_PAGE_KEY] = pg

        msg_text, keyboard, subdirs = build_directory_browser(current_path, pg)
        if context.user_data is not None:
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    elif data == CB_DIR_CONFIRM:
        default_path = str(Path.cwd())
        selected_path = (
            context.user_data.get(BROWSE_PATH_KEY, default_path)
            if context.user_data
            else default_path
        )
        # Check if this was initiated from a thread bind flow
        pending_thread_id: int | None = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )

        # Validate: confirm button must come from the same topic that started browsing
        confirm_thread_id = _get_thread_id(update)
        if pending_thread_id is not None and confirm_thread_id != pending_thread_id:
            clear_browse_state(context.user_data)
            if context.user_data is not None:
                context.user_data.pop("_pending_thread_id", None)
                context.user_data.pop("_pending_thread_text", None)
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return

        clear_browse_state(context.user_data)

        # Check for existing sessions in this directory
        sessions = await session_manager.list_sessions_for_directory(selected_path)
        if sessions:
            # Show session picker -- store state for later
            if context.user_data is not None:
                context.user_data[STATE_KEY] = STATE_SELECTING_SESSION
                context.user_data[SESSIONS_KEY] = sessions
                context.user_data["_selected_path"] = selected_path
            text, keyboard = build_session_picker(sessions)
            await safe_edit(query, text, reply_markup=keyboard)
            await query.answer()
            return

        # No existing sessions -- create new window directly
        await _create_and_bind_window(
            query, context, user, selected_path, pending_thread_id
        )

    elif data == CB_DIR_CANCEL:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale browser (topic mismatch)", show_alert=True)
            return
        clear_browse_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
        await safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Session picker: resume existing session
    elif data.startswith(CB_SESSION_SELECT):
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        # Fallback: if _pending_thread_id was cleared (e.g. by a message in
        # another topic), recover it from the callback query's message context
        if pending_tid is None:
            pending_tid = _get_thread_id(update)
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        try:
            idx = int(data[len(CB_SESSION_SELECT) :])
        except ValueError:
            await query.answer("Invalid data")
            return

        cached_sessions = (
            context.user_data.get(SESSIONS_KEY, []) if context.user_data else []
        )
        if idx < 0 or idx >= len(cached_sessions):
            await query.answer("Session not found")
            return

        session = cached_sessions[idx]
        selected_path = (
            context.user_data.get("_selected_path", str(Path.cwd()))
            if context.user_data
            else str(Path.cwd())
        )
        clear_session_picker_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_selected_path", None)

        await _create_and_bind_window(
            query,
            context,
            user,
            selected_path,
            pending_tid,
            resume_session_id=session.session_id,
        )

    elif data == CB_SESSION_NEW:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is None:
            pending_tid = _get_thread_id(update)
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        selected_path = (
            context.user_data.get("_selected_path", str(Path.cwd()))
            if context.user_data
            else str(Path.cwd())
        )
        clear_session_picker_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_selected_path", None)

        await _create_and_bind_window(query, context, user, selected_path, pending_tid)

    elif data == CB_SESSION_CANCEL:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        clear_session_picker_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
            context.user_data.pop("_selected_path", None)
        await safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Window picker: bind existing window
    elif data.startswith(CB_WIN_BIND):
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        try:
            idx = int(data[len(CB_WIN_BIND) :])
        except ValueError:
            await query.answer("Invalid data")
            return

        cached_windows: list[str] = (
            context.user_data.get(UNBOUND_WINDOWS_KEY, []) if context.user_data else []
        )
        if idx < 0 or idx >= len(cached_windows):
            await query.answer("Window list changed, please retry", show_alert=True)
            return
        selected_wid = cached_windows[idx]

        # Verify window still exists
        w = await tmux_manager.find_window_by_id(selected_wid)
        if not w:
            display = session_manager.get_display_name(selected_wid)
            await query.answer(f"Window '{display}' no longer exists", show_alert=True)
            return

        thread_id = _get_thread_id(update)
        if thread_id is None:
            await query.answer("Not in a topic", show_alert=True)
            return

        display = w.window_name
        clear_window_picker_state(context.user_data)
        session_manager.bind_thread(
            user.id, thread_id, selected_wid, window_name=display
        )

        # Rename the topic to match the window name
        resolved_chat = session_manager.resolve_chat_id(user.id, thread_id)
        try:
            await context.bot.edit_forum_topic(
                chat_id=resolved_chat,
                message_thread_id=thread_id,
                name=display,
            )
        except Exception as e:
            logger.debug(f"Failed to rename topic: {e}")

        await safe_edit(
            query,
            f"✅ Bound to window `{display}`",
        )

        # Forward pending text if any
        pending_text = (
            context.user_data.get("_pending_thread_text") if context.user_data else None
        )
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_text", None)
            context.user_data.pop("_pending_thread_id", None)
        if pending_text:
            send_ok, send_msg = await session_manager.send_to_window(
                selected_wid, pending_text
            )
            if not send_ok:
                logger.warning("Failed to forward pending text: %s", send_msg)
                await safe_send(
                    context.bot,
                    resolved_chat,
                    f"❌ Failed to send pending message: {send_msg}",
                    message_thread_id=thread_id,
                )
        await query.answer("Bound")

    # Window picker: new session -> transition to directory browser
    elif data == CB_WIN_NEW:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        # Preserve pending thread info, clear only picker state
        clear_window_picker_state(context.user_data)
        start_path = str(Path.cwd())
        msg_text, keyboard, subdirs = build_directory_browser(start_path)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
            context.user_data[BROWSE_PATH_KEY] = start_path
            context.user_data[BROWSE_PAGE_KEY] = 0
            context.user_data[BROWSE_DIRS_KEY] = subdirs
        await safe_edit(query, msg_text, reply_markup=keyboard)
        await query.answer()

    # Window picker: cancel
    elif data == CB_WIN_CANCEL:
        pending_tid = (
            context.user_data.get("_pending_thread_id") if context.user_data else None
        )
        if pending_tid is not None and _get_thread_id(update) != pending_tid:
            await query.answer("Stale picker (topic mismatch)", show_alert=True)
            return
        clear_window_picker_state(context.user_data)
        if context.user_data is not None:
            context.user_data.pop("_pending_thread_id", None)
            context.user_data.pop("_pending_thread_text", None)
        await safe_edit(query, "Cancelled")
        await query.answer("Cancelled")

    # Screenshot: Refresh
    elif data.startswith(CB_SCREENSHOT_REFRESH):
        window_id = data[len(CB_SCREENSHOT_REFRESH) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        w = await tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window no longer exists", show_alert=True)
            return

        text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if not text:
            await query.answer("Failed to capture pane", show_alert=True)
            return

        png_bytes = await text_to_image(text, with_ansi=True)
        keyboard = _build_screenshot_keyboard(window_id)
        try:
            await query.edit_message_media(
                media=InputMediaDocument(
                    media=io.BytesIO(png_bytes), filename="screenshot.png"
                ),
                reply_markup=keyboard,
            )
            await query.answer("Refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh screenshot: {e}")
            await query.answer("Failed to refresh", show_alert=True)

    # Mode switching callback
    elif data.startswith("mode:"):
        parts = data.split(":")
        if len(parts) >= 3:
            mode_name = parts[1]
            mode_labels = {
                "auto": "Auto (no prompts)",
                "edit": "Accept edits",
                "plan": "Plan only",
                "approve": "Approve all",
            }
            label = mode_labels.get(mode_name, mode_name)
            await safe_edit(
                query,
                f"⚠ Mode `{label}` selected.\n"
                "Mode switching requires restarting Claude.\n"
                "Use /esc to exit current session, then create a new topic.",
            )
        await query.answer()

    elif data == "noop":
        await query.answer()

    # Interactive UI: Up arrow
    elif data.startswith(CB_ASK_UP):
        window_id = data[len(CB_ASK_UP) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(w.window_id, "Up", enter=False, literal=False)
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer()

    # Interactive UI: Down arrow
    elif data.startswith(CB_ASK_DOWN):
        window_id = data[len(CB_ASK_DOWN) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Down", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer()

    # Interactive UI: Left arrow
    elif data.startswith(CB_ASK_LEFT):
        window_id = data[len(CB_ASK_LEFT) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Left", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer()

    # Interactive UI: Right arrow
    elif data.startswith(CB_ASK_RIGHT):
        window_id = data[len(CB_ASK_RIGHT) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Right", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer()

    # Interactive UI: Escape
    elif data.startswith(CB_ASK_ESC):
        window_id = data[len(CB_ASK_ESC) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Escape", enter=False, literal=False
            )
            await clear_interactive_msg(user.id, context.bot, thread_id)
        await query.answer("⎋ Esc")

    # Interactive UI: Enter
    elif data.startswith(CB_ASK_ENTER):
        window_id = data[len(CB_ASK_ENTER) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Enter", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer("⏎ Enter")

    # Interactive UI: Space
    elif data.startswith(CB_ASK_SPACE):
        window_id = data[len(CB_ASK_SPACE) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(
                w.window_id, "Space", enter=False, literal=False
            )
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer("␣ Space")

    # Interactive UI: Tab
    elif data.startswith(CB_ASK_TAB):
        window_id = data[len(CB_ASK_TAB) :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            await tmux_manager.send_keys(w.window_id, "Tab", enter=False, literal=False)
            await asyncio.sleep(0.5)
            await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer("⇥ Tab")

    # Interactive UI: refresh display
    # Interactive UI: Smart choice (send number key + Enter)
    elif data.startswith(CB_ASK_KEYS):
        rest = data[len(CB_ASK_KEYS):]
        colon_idx = rest.find(":")
        if colon_idx < 0:
            await query.answer("Invalid data")
            return
        key_to_send = rest[:colon_idx]
        window_id = rest[colon_idx + 1:]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        w = await tmux_manager.find_window_by_id(window_id)
        if w:
            # Send the number key, then Enter to confirm
            await tmux_manager.send_keys(w.window_id, key_to_send, enter=False, literal=True)
            await asyncio.sleep(0.3)
            await tmux_manager.send_keys(w.window_id, "Enter", enter=False, literal=False)
            await asyncio.sleep(0.5)
            # Clear interactive UI since choice was made
            effective_uid = _resolve_interactive_user_id(
                user.id, thread_id, query.message.chat_id if query.message else None
            )
            await clear_interactive_msg(effective_uid, context.bot, thread_id)
        await query.answer()

    elif data.startswith(CB_ASK_REFRESH):
        window_id = data[len(CB_ASK_REFRESH):]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return
        thread_id = _get_thread_id(update)
        await handle_interactive_ui(context.bot, _resolve_interactive_user_id(user.id, thread_id, query.message.chat_id if query.message else None), window_id, thread_id)
        await query.answer("🔄")

    # Screenshot quick keys: send key to tmux window
    elif data.startswith(CB_KEYS_PREFIX):
        rest = data[len(CB_KEYS_PREFIX) :]
        colon_idx = rest.find(":")
        if colon_idx < 0:
            await query.answer("Invalid data")
            return
        key_id = rest[:colon_idx]
        window_id = rest[colon_idx + 1 :]
        if not _is_valid_window_id(window_id):
            await query.answer("Invalid window ID")
            return

        key_info = _KEYS_SEND_MAP.get(key_id)
        if not key_info:
            await query.answer("Unknown key")
            return

        tmux_key, enter, literal = key_info
        w = await tmux_manager.find_window_by_id(window_id)
        if not w:
            await query.answer("Window not found", show_alert=True)
            return

        await tmux_manager.send_keys(
            w.window_id, tmux_key, enter=enter, literal=literal
        )
        await query.answer(_KEY_LABELS.get(key_id, key_id))

        # Refresh screenshot after key press
        await asyncio.sleep(0.5)
        text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
        if text:
            png_bytes = await text_to_image(text, with_ansi=True)
            keyboard = _build_screenshot_keyboard(window_id)
            try:
                await query.edit_message_media(
                    media=InputMediaDocument(
                        media=io.BytesIO(png_bytes),
                        filename="screenshot.png",
                    ),
                    reply_markup=keyboard,
                )
            except Exception:
                pass  # Screenshot unchanged or message too old


# --- App lifecycle ---


async def post_init(application: Application) -> None:
    global session_monitor, _status_poll_task

    await application.bot.delete_my_commands()

    bot_commands = [
        BotCommand("start", "Show welcome message"),
        BotCommand("history", "Message history for this topic"),
        BotCommand("screenshot", "Terminal screenshot with control keys"),
        BotCommand("esc", "Send Escape to interrupt Claude"),
        BotCommand("kill", "Kill session and delete topic"),
        BotCommand("unbind", "Unbind topic from session (keeps window running)"),
        BotCommand("bind", "Bind an existing tmux session to this topic"),
        BotCommand("mode", "Switch permission mode (auto/edit/plan/approve)"),
        BotCommand("usage", "Show Claude Code usage remaining"),
    ]
    # Add Claude Code slash commands
    for cmd_name, desc in CC_COMMANDS.items():
        bot_commands.append(BotCommand(cmd_name, desc))

    await application.bot.set_my_commands(bot_commands)

    # Re-resolve stale window IDs from persisted state against live tmux windows
    await session_manager.resolve_stale_ids()

    # Pre-fill global rate limiter bucket on restart.
    # AsyncLimiter starts at _level=0 (full burst capacity), but Telegram's
    # server-side counter persists across bot restarts.  Setting _level=max_rate
    # forces the bucket to start "full" so capacity drains in naturally (~1s).
    # AIORateLimiter has no per-private-chat limiter, so max_retries is the
    # primary protection (retry + pause all concurrent requests on 429).
    rate_limiter = application.bot.rate_limiter
    if rate_limiter and rate_limiter._base_limiter:
        rate_limiter._base_limiter._level = rate_limiter._base_limiter.max_rate
        logger.info("Pre-filled global rate limiter bucket")

    monitor = SessionMonitor()

    async def message_callback(msg: NewMessage) -> None:
        await handle_new_message(msg, application.bot)

    async def new_session_callback(session: NewSession) -> None:
        await handle_new_session(session, application.bot)

    async def session_removed_callback(window_id: str) -> None:
        await handle_session_removed(window_id, application.bot)

    monitor.set_message_callback(message_callback)
    monitor.set_new_session_callback(new_session_callback)
    monitor.set_session_removed_callback(session_removed_callback)
    monitor.start()
    session_monitor = monitor
    logger.info("Session monitor started")

    # Reconcile: create dev topics for tmux sessions without topics
    await reconcile_dev_topics(application.bot)

    # Start status polling task
    _status_poll_task = asyncio.create_task(status_poll_loop(application.bot))
    logger.info("Status polling task started")

    # Start reminder monitor if conversational groups are configured
    if config.conversational_groups:
        _reminder_task = start_reminder_loop(application.bot)
        logger.info(
            "Reminder monitor started (interval: %ds)", config.reminder_interval
        )


async def post_shutdown(application: Application) -> None:
    global _status_poll_task, _reminder_task

    # Stop status polling
    if _status_poll_task:
        _status_poll_task.cancel()
        try:
            await _status_poll_task
        except asyncio.CancelledError:
            pass
        _status_poll_task = None
        logger.info("Status polling stopped")

    # Stop reminder monitor
    if _reminder_task:
        _reminder_task.cancel()
        try:
            await _reminder_task
        except asyncio.CancelledError:
            pass
        _reminder_task = None
        logger.info("Reminder monitor stopped")

    # Stop all queue workers
    await shutdown_workers()

    if session_monitor:
        session_monitor.stop()
        logger.info("Session monitor stopped")

    await close_transcribe_client()


def create_bot() -> Application:
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("screenshot", screenshot_command))
    application.add_handler(CommandHandler("esc", esc_command))
    application.add_handler(CommandHandler("unbind", unbind_command))
    application.add_handler(CommandHandler("bind", bind_command))
    application.add_handler(CommandHandler("mode", mode_command))
    application.add_handler(CommandHandler("usage", usage_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    # Topic created event -- cache topic names
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CREATED,
            topic_created_handler,
        )
    )
    # Topic closed event -- auto-kill associated window
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_CLOSED,
            topic_closed_handler,
        )
    )
    # Topic edited event -- sync renamed topic to tmux window
    application.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_EDITED,
            topic_edited_handler,
        )
    )
    # Forward any other /command to Claude Code
    application.add_handler(MessageHandler(filters.COMMAND, forward_command_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    # Photos: download and forward file path to Claude Code
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    # Voice: transcribe via OpenAI and forward text to Claude Code
    application.add_handler(MessageHandler(filters.VOICE, voice_handler))
    # Catch-all: non-text content (stickers, video, etc.)
    application.add_handler(
        MessageHandler(
            ~filters.COMMAND & ~filters.TEXT & ~filters.StatusUpdate.ALL,
            unsupported_content_handler,
        )
    )

    return application
