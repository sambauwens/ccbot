"""Session command handlers -- /start, /history, /screenshot, /esc, /usage, /bind, /unbind, /mode.

Telegram command handlers for managing Claude Code sessions bound to topics.
Each command operates on the tmux window associated with the current topic.

Key functions:
  - start_command: Show welcome message
  - history_command: Paginated message history
  - screenshot_command: Capture tmux pane as PNG with control keys
  - esc_command: Send Escape to interrupt Claude
  - usage_command: Show Claude Code usage stats
  - bind_command: Bind existing tmux session to topic
  - unbind_command: Unbind topic from session
  - mode_command: Switch permission mode
"""

import asyncio
import io
import logging

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from ..markdown_v2 import convert_markdown
from ..screenshot import text_to_image
from ..session import session_manager
from ..terminal_parser import extract_bash_output
from ..tmux_manager import tmux_manager
from .callback_data import CB_KEYS_PREFIX, CB_SCREENSHOT_REFRESH
from .cleanup import clear_topic_state
from .directory_browser import (
    STATE_KEY,
    STATE_SELECTING_WINDOW,
    UNBOUND_WINDOWS_KEY,
    build_window_picker,
)
from .history import send_history
from .message_sender import NO_LINK_PREVIEW, safe_reply, send_with_fallback

logger = logging.getLogger(__name__)

# Claude Code commands shown in bot menu (forwarded via tmux)
CC_COMMANDS: dict[str, str] = {
    "clear": "↗ Clear conversation history",
    "compact": "↗ Compact conversation context",
    "cost": "↗ Show token/cost usage",
    "help": "↗ Show Claude Code help",
    "memory": "↗ Edit CLAUDE.md",
    "model": "↗ Switch AI model",
}

# Screenshot quick-key mappings: key_id -> (tmux_key, enter, literal)
_KEYS_SEND_MAP: dict[str, tuple[str, bool, bool]] = {
    "up": ("Up", False, False),
    "dn": ("Down", False, False),
    "lt": ("Left", False, False),
    "rt": ("Right", False, False),
    "esc": ("Escape", False, False),
    "ent": ("Enter", False, False),
    "spc": ("Space", False, False),
    "tab": ("Tab", False, False),
    "cc": ("C-c", False, False),
}

# key_id -> display label (shown in callback answer toast)
_KEY_LABELS: dict[str, str] = {
    "up": "↑",
    "dn": "↓",
    "lt": "←",
    "rt": "→",
    "esc": "⎋ Esc",
    "ent": "⏎ Enter",
    "spc": "␣ Space",
    "tab": "⇥ Tab",
    "cc": "^C",
}


def _build_screenshot_keyboard(window_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for screenshot: control keys + refresh."""

    def btn(label: str, key_id: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{CB_KEYS_PREFIX}{key_id}:{window_id}"[:64],
        )

    return InlineKeyboardMarkup(
        [
            [btn("␣ Space", "spc"), btn("↑", "up"), btn("⇥ Tab", "tab")],
            [btn("←", "lt"), btn("↓", "dn"), btn("→", "rt")],
            [btn("⎋ Esc", "esc"), btn("^C", "cc"), btn("⏎ Enter", "ent")],
            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"{CB_SCREENSHOT_REFRESH}{window_id}"[:64],
                )
            ],
        ]
    )


# Active bash capture tasks: (user_id, thread_id) -> asyncio.Task
_bash_capture_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}


def _cancel_bash_capture(user_id: int, thread_id: int) -> None:
    """Cancel any running bash capture for this topic."""
    key = (user_id, thread_id)
    task = _bash_capture_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


async def _capture_bash_output(
    bot: Bot,
    user_id: int,
    thread_id: int,
    window_id: str,
    command: str,
) -> None:
    """Background task: capture ``!`` bash command output from tmux pane.

    Sends the first captured output as a new message, then edits it
    in-place as more output appears.  Stops after 30 s or when cancelled
    (e.g. user sends a new message, which pushes content down).
    """
    try:
        # Wait for the command to start producing output
        await asyncio.sleep(2.0)

        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        msg_id: int | None = None
        last_output: str = ""

        for _ in range(30):
            raw = await tmux_manager.capture_pane(window_id)
            if raw is None:
                return

            output = extract_bash_output(raw, command)
            if not output:
                await asyncio.sleep(1.0)
                continue

            # Skip edit if nothing changed
            if output == last_output:
                await asyncio.sleep(1.0)
                continue

            last_output = output

            # Truncate to fit Telegram's 4096-char limit
            if len(output) > 3800:
                output = "… " + output[-3800:]

            if msg_id is None:
                # First capture -- send a new message
                sent = await send_with_fallback(
                    bot,
                    chat_id,
                    output,
                    message_thread_id=thread_id,
                )
                if sent:
                    msg_id = sent.message_id
            else:
                # Subsequent captures -- edit in place
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=convert_markdown(output),
                        parse_mode="MarkdownV2",
                        link_preview_options=NO_LINK_PREVIEW,
                    )
                except Exception:
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=output,
                            link_preview_options=NO_LINK_PREVIEW,
                        )
                    except Exception:
                        pass

            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        return
    finally:
        _bash_capture_tasks.pop((user_id, thread_id), None)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..bot import is_user_allowed
    from .directory_browser import clear_browse_state

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    clear_browse_state(context.user_data)

    if update.message:
        await safe_reply(
            update.message,
            "🤖 *Claude Code Monitor*\n\n"
            "Each topic is a session. Create a new topic to start.",
        )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show message history for the active session or bound thread."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    await send_history(update.message, wid)


async def screenshot_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Capture the current tmux pane and send it as an image."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    text = await tmux_manager.capture_pane(w.window_id, with_ansi=True)
    if not text:
        await safe_reply(update.message, "❌ Failed to capture pane content.")
        return

    png_bytes = await text_to_image(text, with_ansi=True)
    keyboard = _build_screenshot_keyboard(wid)
    await update.message.reply_document(
        document=io.BytesIO(png_bytes),
        filename="screenshot.png",
        reply_markup=keyboard,
    )


async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unbind this topic from its Claude session without killing the window."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "❌ This command only works in a topic.")
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    display = session_manager.get_display_name(wid)
    session_manager.unbind_thread(user.id, thread_id)
    await clear_topic_state(user.id, thread_id, context.bot, context.user_data)

    await safe_reply(
        update.message,
        f"✅ Topic unbound from window '{display}'.\n"
        "The Claude session is still running in tmux.\n"
        "Send a message to bind to a new session.",
    )


async def esc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Escape key to interrupt Claude."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
        return

    # Send Escape control character (no enter)
    await tmux_manager.send_keys(w.window_id, "\x1b", enter=False)
    await safe_reply(update.message, "⎋ Sent Escape")


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch Claude Code usage stats from TUI and send to Telegram."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "No session bound to this topic.")
        return

    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        await safe_reply(update.message, f"Window '{wid}' no longer exists.")
        return

    # Send /usage command to Claude Code TUI
    await tmux_manager.send_keys(w.window_id, "/usage")
    # Wait for the modal to render
    await asyncio.sleep(2.0)
    # Capture the pane content
    pane_text = await tmux_manager.capture_pane(w.window_id)
    # Dismiss the modal
    await tmux_manager.send_keys(w.window_id, "Escape", enter=False, literal=False)

    if not pane_text:
        await safe_reply(update.message, "Failed to capture usage info.")
        return

    # Try to parse structured usage info
    from ..terminal_parser import parse_usage_output

    usage = parse_usage_output(pane_text)
    if usage and usage.parsed_lines:
        text = "\n".join(usage.parsed_lines)
        await safe_reply(update.message, f"```\n{text}\n```")
    else:
        # Fallback: send raw pane capture trimmed
        trimmed = pane_text.strip()
        if len(trimmed) > 3000:
            trimmed = trimmed[:3000] + "\n... (truncated)"
        await safe_reply(update.message, f"```\n{trimmed}\n```")


async def bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bind an existing tmux session to this topic.

    Lists all tmux sessions across all projects, user picks one -> binds to current topic.
    Enables 'start from terminal, continue from phone'.
    """
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        await safe_reply(update.message, "❌ This command only works in a topic.")
        return

    # Capture group chat_id
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    # Check if already bound
    existing_wid = session_manager.get_window_for_thread(user.id, thread_id)
    if existing_wid:
        display = session_manager.get_display_name(existing_wid)
        await safe_reply(
            update.message,
            f"❌ This topic is already bound to `{display}`.\n"
            "Use /unbind first to disconnect it.",
        )
        return

    # List all tmux sessions/windows, show unbound ones
    all_windows = await tmux_manager.list_windows()
    bound_ids = {wid for _, _, wid in session_manager.iter_thread_bindings()}
    unbound = [
        (w.window_id, w.window_name, w.cwd)
        for w in all_windows
        if w.window_id not in bound_ids
    ]

    if not unbound:
        await safe_reply(
            update.message,
            "❌ No unbound tmux sessions available.\n"
            "Start a session from terminal with `dev go new` first.",
        )
        return

    msg_text, keyboard, win_ids = build_window_picker(unbound)
    if context.user_data is not None:
        context.user_data[STATE_KEY] = STATE_SELECTING_WINDOW
        context.user_data[UNBOUND_WINDOWS_KEY] = win_ids
        context.user_data["_pending_thread_id"] = thread_id
        context.user_data["_pending_thread_text"] = None  # No pending text for /bind
    await safe_reply(update.message, msg_text, reply_markup=keyboard)


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch Claude Code permission mode for the session bound to this topic."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return
    if not update.message:
        return

    thread_id = _get_thread_id(update)
    wid = session_manager.resolve_window_for_thread(user.id, thread_id)
    if not wid:
        await safe_reply(update.message, "❌ No session bound to this topic.")
        return

    # Parse argument
    text = update.message.text or ""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        # Show current options
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Auto", callback_data=f"mode:auto:{wid}"),
                    InlineKeyboardButton(
                        "Accept edits", callback_data=f"mode:edit:{wid}"
                    ),
                ],
                [
                    InlineKeyboardButton("Plan", callback_data=f"mode:plan:{wid}"),
                    InlineKeyboardButton(
                        "Approve all", callback_data=f"mode:approve:{wid}"
                    ),
                ],
            ]
        )
        await safe_reply(
            update.message, "Select permission mode:", reply_markup=keyboard
        )
        return

    mode_arg = parts[1].lower().strip()
    mode_flags = {
        "auto": "--dangerously-skip-permissions",
        "edit": "--allowedTools Edit,Write,NotebookEdit",
        "plan": "--allowedTools ''",
        "approve": "",
    }
    if mode_arg not in mode_flags:
        await safe_reply(
            update.message,
            f"❌ Unknown mode `{mode_arg}`.\nValid: auto, edit, plan, approve",
        )
        return

    await safe_reply(
        update.message,
        f"⚠ Mode switching requires restarting Claude.\n"
        f"Use `/esc` to exit current session, then start a new topic with mode `{mode_arg}`.",
    )
