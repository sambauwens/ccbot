"""Message routing -- text_handler, handle_new_message, _get_thread_id.

Routes incoming Telegram text messages and outbound Claude messages:
  - text_handler: Main entry point for user text — resolves topic binding,
    shows window/directory picker for unbound topics, forwards to Claude Code
  - handle_new_message: Routes Claude responses back to the correct Telegram topic
"""

import asyncio
import logging
from pathlib import Path

from telegram import Bot, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..config import config
from ..session import session_manager
from ..terminal_parser import is_interactive_ui
from ..tmux_manager import tmux_manager
from .directory_browser import (
    BROWSE_DIRS_KEY,
    BROWSE_PAGE_KEY,
    BROWSE_PATH_KEY,
    STATE_BROWSING_DIRECTORY,
    STATE_KEY,
    STATE_SELECTING_SESSION,
    STATE_SELECTING_WINDOW,
    UNBOUND_WINDOWS_KEY,
    build_directory_browser,
    build_window_picker,
    clear_browse_state,
    clear_session_picker_state,
    clear_window_picker_state,
)
from .interactive_ui import (
    INTERACTIVE_TOOL_NAMES,
    clear_interactive_mode,
    clear_interactive_msg,
    get_interactive_msg_id,
    get_interactive_window,
    handle_interactive_ui,
    set_interactive_mode,
)
from .message_queue import (
    enqueue_content_message,
    enqueue_status_update,
    get_message_queue,
)
from .message_sender import safe_reply
from .response_builder import build_response_parts
from .session_commands import (
    _cancel_bash_capture,
    _bash_capture_tasks,
    _capture_bash_output,
)

logger = logging.getLogger(__name__)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..bot import is_user_allowed, _get_thread_id
    from .conversational import handle_conversational_message

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        if update.message:
            await safe_reply(update.message, "You are not authorized to use this bot.")
        return

    if not update.message or not update.message.text:
        return

    thread_id = _get_thread_id(update)

    # Capture group chat_id for supergroup forum topic routing.
    # Required: Telegram Bot API needs group chat_id (not user_id) to send
    # messages with message_thread_id. Do NOT remove -- see session.py docs.
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        session_manager.set_group_chat_id(user.id, thread_id, chat.id)

    text = update.message.text

    # Ignore text in window picker mode (only for the same thread)
    if context.user_data and context.user_data.get(STATE_KEY) == STATE_SELECTING_WINDOW:
        pending_tid = context.user_data.get("_pending_thread_id")
        if pending_tid == thread_id:
            await safe_reply(
                update.message,
                "Please use the window picker above, or tap Cancel.",
            )
            return
        # Stale picker state from a different thread -- clear it
        clear_window_picker_state(context.user_data)
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)

    # Ignore text in directory browsing mode (only for the same thread)
    if (
        context.user_data
        and context.user_data.get(STATE_KEY) == STATE_BROWSING_DIRECTORY
    ):
        pending_tid = context.user_data.get("_pending_thread_id")
        if pending_tid == thread_id:
            await safe_reply(
                update.message,
                "Please use the directory browser above, or tap Cancel.",
            )
            return
        # Stale browsing state from a different thread -- clear it
        clear_browse_state(context.user_data)
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)

    # Ignore text in session picker mode (only for the same thread)
    if (
        context.user_data
        and context.user_data.get(STATE_KEY) == STATE_SELECTING_SESSION
    ):
        pending_tid = context.user_data.get("_pending_thread_id")
        if pending_tid == thread_id:
            await safe_reply(
                update.message,
                "Please use the session picker above, or tap Cancel.",
            )
            return
        # Stale picker state from a different thread -- clear it
        clear_session_picker_state(context.user_data)
        context.user_data.pop("_pending_thread_id", None)
        context.user_data.pop("_pending_thread_text", None)
        context.user_data.pop("_selected_path", None)

    # General topic (thread_id is None) or any topic in a managed group
    chat_id = chat.id if chat else None

    if thread_id is None:
        # General topic
        if chat_id and (
            config.is_conversational_group(chat_id) or config.is_dev_group(chat_id)
        ):
            # Conversational session in General -- route through topic_bindings
            await handle_conversational_message(
                update, context, user, chat_id, None, text
            )
            return

        # Not a managed group -- reject
        await safe_reply(
            update.message,
            "❌ Please use a named topic. Create a new topic to start a session.",
        )
        return

    # Check topic_bindings first (conversational topics)
    wid = None
    if chat_id:
        wid = session_manager.get_window_for_topic(chat_id, thread_id)
    if wid and chat_id:
        # Conversational topic -- route through shared session
        await handle_conversational_message(
            update,
            context,
            user,
            chat_id,
            thread_id,
            text,
        )
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if wid is None:
        # Unbound topic -- check if this is a conversational or dev group
        if chat_id and (
            config.is_conversational_group(chat_id) or config.is_dev_group(chat_id)
        ):
            # User-created topic in managed group -> conversational topic
            await handle_conversational_message(
                update,
                context,
                user,
                chat_id,
                thread_id,
                text,
            )
            return

        # Not a managed group -- fall back to window picker / directory browser
        all_windows = await tmux_manager.list_windows()
        bound_ids = {wid for _, _, wid in session_manager.iter_thread_bindings()}
        unbound = [
            (w.window_id, w.session_name or w.window_name, w.cwd)
            for w in all_windows
            if w.window_id not in bound_ids
        ]
        logger.debug(
            "Window picker check: all=%s, bound=%s, unbound=%s",
            [w.window_name for w in all_windows],
            bound_ids,
            [name for _, name, _ in unbound],
        )

        if unbound:
            # Show window picker
            logger.info(
                "Unbound topic: showing window picker (%d unbound windows, user=%d, thread=%d)",
                len(unbound),
                user.id,
                thread_id,
            )
            msg_text, keyboard, win_ids = build_window_picker(unbound)
            if context.user_data is not None:
                context.user_data[STATE_KEY] = STATE_SELECTING_WINDOW
                context.user_data[UNBOUND_WINDOWS_KEY] = win_ids
                context.user_data["_pending_thread_id"] = thread_id
                context.user_data["_pending_thread_text"] = text
            await safe_reply(update.message, msg_text, reply_markup=keyboard)
            return

        # No unbound windows -- show directory browser to create a new session
        logger.info(
            "Unbound topic: showing directory browser (user=%d, thread=%d)",
            user.id,
            thread_id,
        )
        start_path = str(Path.cwd())
        msg_text, keyboard, subdirs = build_directory_browser(start_path)
        if context.user_data is not None:
            context.user_data[STATE_KEY] = STATE_BROWSING_DIRECTORY
            context.user_data[BROWSE_PATH_KEY] = start_path
            context.user_data[BROWSE_PAGE_KEY] = 0
            context.user_data[BROWSE_DIRS_KEY] = subdirs
            context.user_data["_pending_thread_id"] = thread_id
            context.user_data["_pending_thread_text"] = text
        await safe_reply(update.message, msg_text, reply_markup=keyboard)
        return

    # Bound topic -- forward to bound window
    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        display = session_manager.get_display_name(wid)
        logger.info(
            "Stale binding: window %s gone, unbinding (user=%d, thread=%d)",
            display,
            user.id,
            thread_id,
        )
        session_manager.unbind_thread(user.id, thread_id)
        await safe_reply(
            update.message,
            f"❌ Window '{display}' no longer exists. Binding removed.\n"
            "Send a message to start a new session.",
        )
        return

    await update.message.chat.send_action(ChatAction.TYPING)
    await enqueue_status_update(context.bot, user.id, wid, None, thread_id=thread_id)

    # Cancel any running bash capture -- new message pushes pane content down
    _cancel_bash_capture(user.id, thread_id)

    # Check for pending interactive UI before sending text.
    # This catches UIs (permission prompts, etc.) that status polling might have missed.
    pane_text = await tmux_manager.capture_pane(w.window_id)
    if pane_text and is_interactive_ui(pane_text):
        # UI detected -- show it to user, then send text (acts as Enter)
        logger.info(
            "Detected pending interactive UI before sending text (user=%d, thread=%s)",
            user.id,
            thread_id,
        )
        await handle_interactive_ui(context.bot, user.id, wid, thread_id)
        # Small delay to let UI render in Telegram before text arrives
        await asyncio.sleep(0.3)

    # For dev sessions via Telegram: append wait instruction so user can switch to terminal.
    from .media import _append_telegram_wait

    send_text = text
    if not text.startswith("!") and not text.startswith("/"):
        send_text = _append_telegram_wait(text, wid)

    session_manager.mark_telegram_input(wid)
    success, message = await session_manager.send_to_window(wid, send_text)
    if not success:
        await safe_reply(update.message, f"❌ {message}")
        return

    # Start background capture for ! bash command output
    if text.startswith("!") and len(text) > 1:
        bash_cmd = text[1:]  # strip leading "!"
        task = asyncio.create_task(
            _capture_bash_output(context.bot, user.id, thread_id, wid, bash_cmd)
        )
        _bash_capture_tasks[(user.id, thread_id)] = task

    # If in interactive mode, refresh the UI after sending text
    interactive_window = get_interactive_window(user.id, thread_id)
    if interactive_window and interactive_window == wid:
        await asyncio.sleep(0.2)
        await handle_interactive_ui(context.bot, user.id, wid, thread_id)


async def handle_new_message(msg: object, bot: Bot) -> None:
    """Handle a new assistant message -- enqueue for sequential processing.

    Messages are queued per-user to ensure status messages always appear last.
    Routes via thread_bindings to deliver to the correct topic.
    """
    from ..session_monitor import NewMessage
    from .conversational import _inject_github_links

    assert isinstance(msg, NewMessage)

    status = "complete" if msg.is_complete else "streaming"
    logger.info(
        f"handle_new_message [{status}]: session={msg.session_id}, "
        f"text_len={len(msg.text)}"
    )

    # Check topic_bindings first (conversational topics — deliver to topic, not per-user)
    active_topics = await session_manager.find_topics_for_session(msg.session_id)
    if active_topics and (msg.is_complete or msg.tool_name in INTERACTIVE_TOOL_NAMES):
        # Skip user messages sent from Telegram (already visible); forward terminal ones
        if msg.role == "user":
            for _, _, wid in active_topics:
                if session_manager.was_telegram_input(wid):
                    return
            # Message came from terminal — fall through to forward it

        # Post-process text with GitHub links for conversational topics
        processed_text = msg.text
        if msg.content_type == "text" and msg.role == "assistant":
            for _, _, wid in active_topics:
                ws = session_manager.get_window_state(wid)
                if ws.cwd:
                    processed_text = _inject_github_links(processed_text, ws.cwd)
                    break

        parts = build_response_parts(
            processed_text,
            msg.is_complete,
            msg.content_type,
            msg.role,
        )
        for topic_chat_id, topic_thread_id, wid in active_topics:
            # Use a synthetic user_id for the queue (negative chat_id as key)
            queue_user_id = topic_chat_id  # use chat_id as queue key
            await enqueue_content_message(
                bot=bot,
                user_id=queue_user_id,
                window_id=wid,
                parts=parts,
                tool_use_id=msg.tool_use_id,
                content_type=msg.content_type,
                text=msg.text,
                thread_id=topic_thread_id,
                image_data=msg.image_data,
            )
        return

    # Find users whose thread-bound window matches this session (dev topics)
    active_users = await session_manager.find_users_for_session(msg.session_id)

    if not active_users and not active_topics:
        logger.info(f"No active users for session {msg.session_id}")
        return

    for user_id, wid, thread_id in active_users:
        # Skip user messages sent from Telegram (already visible); forward terminal ones
        if msg.role == "user" and session_manager.was_telegram_input(wid):
            continue

        # Handle interactive tools specially - capture terminal and send UI
        if msg.tool_name in INTERACTIVE_TOOL_NAMES and msg.content_type == "tool_use":
            # Mark interactive mode BEFORE sleeping so polling skips this window
            set_interactive_mode(user_id, wid, thread_id)
            # Flush pending messages (e.g. plan content) before sending interactive UI
            queue = get_message_queue(user_id)
            if queue:
                await queue.join()
            # Wait briefly for Claude Code to render the question UI
            await asyncio.sleep(0.3)
            handled = await handle_interactive_ui(bot, user_id, wid, thread_id)
            if handled:
                # Update user's read offset
                session = await session_manager.resolve_session_for_window(wid)
                if session and session.file_path:
                    try:
                        file_size = Path(session.file_path).stat().st_size
                        session_manager.update_user_window_offset(
                            user_id, wid, file_size
                        )
                    except OSError:
                        pass
                continue  # Don't send the normal tool_use message
            else:
                # UI not rendered -- clear the early-set mode
                clear_interactive_mode(user_id, thread_id)

        # Any non-interactive message means the interaction is complete -- delete the UI message
        if get_interactive_msg_id(user_id, thread_id):
            await clear_interactive_msg(user_id, bot, thread_id)

        parts = build_response_parts(
            msg.text,
            msg.is_complete,
            msg.content_type,
            msg.role,
        )

        if msg.is_complete:
            # Enqueue content message task
            # Note: tool_result editing is handled inside _process_content_task
            # to ensure sequential processing with tool_use message sending
            await enqueue_content_message(
                bot=bot,
                user_id=user_id,
                window_id=wid,
                parts=parts,
                tool_use_id=msg.tool_use_id,
                content_type=msg.content_type,
                text=msg.text,
                thread_id=thread_id,
                image_data=msg.image_data,
            )

            # Update user's read offset to current file position
            # This marks these messages as "read" for this user
            session = await session_manager.resolve_session_for_window(wid)
            if session and session.file_path:
                try:
                    file_size = Path(session.file_path).stat().st_size
                    session_manager.update_user_window_offset(user_id, wid, file_size)
                except OSError:
                    pass
