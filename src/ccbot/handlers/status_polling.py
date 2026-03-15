"""Terminal status line polling for thread-bound windows.

Provides background polling of terminal status lines for all active users:
  - Detects Claude Code status (working, waiting, etc.)
  - Detects interactive UIs (permission prompts) not triggered via JSONL
  - Updates status messages in Telegram
  - Polls thread_bindings (each topic = one window)
  - Periodically probes topic existence via unpin_all_forum_topic_messages
    (silent no-op when no pins); cleans up deleted topics (kills tmux window
    + unbinds thread)

Key components:
  - STATUS_POLL_INTERVAL: Polling frequency (1 second)
  - TOPIC_CHECK_INTERVAL: Topic existence probe frequency (60 seconds)
  - status_poll_loop: Background polling task
  - update_status_message: Poll and enqueue status updates
"""

import asyncio
import logging
import time

from telegram import Bot
from telegram.error import BadRequest

from ..session import session_manager
from ..terminal_parser import is_interactive_ui
from ..tmux_manager import tmux_manager
from .interactive_ui import (
    clear_interactive_msg,
    get_interactive_window,
    handle_interactive_ui,
)
from .cleanup import clear_topic_state

logger = logging.getLogger(__name__)

# Status polling interval
STATUS_POLL_INTERVAL = 1.0  # seconds - faster response (rate limiting at send layer)

# Topic existence probe interval
TOPIC_CHECK_INTERVAL = 60.0  # seconds


async def update_status_message(
    bot: Bot,
    user_id: int,
    window_id: str,
    thread_id: int | None = None,
) -> None:
    """Poll terminal for interactive UIs (permission prompts, questions).

    Status line messages are suppressed — the bot only forwards conversational
    content (text responses and interactive UIs).
    """
    w = await tmux_manager.find_window_by_id(window_id)
    if not w:
        return

    pane_text = await tmux_manager.capture_pane(w.window_id)
    if not pane_text:
        # Transient capture failure - keep existing status message
        return

    interactive_window = get_interactive_window(user_id, thread_id)
    should_check_new_ui = True

    if interactive_window == window_id:
        # User is in interactive mode for THIS window
        if is_interactive_ui(pane_text):
            # Interactive UI still showing — skip status update (user is interacting)
            return
        # Interactive UI gone — clear interactive mode, fall through to status check.
        # Don't re-check for new UI this cycle (the old one just disappeared).
        await clear_interactive_msg(user_id, bot, thread_id)
        should_check_new_ui = False
    elif interactive_window is not None:
        # User is in interactive mode for a DIFFERENT window (window switched)
        # Clear stale interactive mode
        await clear_interactive_msg(user_id, bot, thread_id)

    # Check for permission prompt (interactive UI not triggered via JSONL)
    # ALWAYS check UI, regardless of skip_status
    if should_check_new_ui and is_interactive_ui(pane_text):
        logger.debug(
            "Interactive UI detected in polling (user=%d, window=%s, thread=%s)",
            user_id,
            window_id,
            thread_id,
        )
        await handle_interactive_ui(bot, user_id, window_id, thread_id)
        return

    # Status line messages suppressed — bot is conversational only
    # (interactive UIs above are still detected and forwarded)


async def status_poll_loop(bot: Bot) -> None:
    """Background task to poll terminal status for all thread-bound windows."""
    logger.info("Status polling started (interval: %ss)", STATUS_POLL_INTERVAL)
    last_topic_check = 0.0
    while True:
        try:
            # Periodic topic existence probe
            now = time.monotonic()
            if now - last_topic_check >= TOPIC_CHECK_INTERVAL:
                last_topic_check = now
                for user_id, thread_id, wid in list(
                    session_manager.iter_thread_bindings()
                ):
                    try:
                        await bot.unpin_all_forum_topic_messages(
                            chat_id=session_manager.resolve_chat_id(user_id, thread_id),
                            message_thread_id=thread_id,
                        )
                    except BadRequest as e:
                        if "Topic_id_invalid" in str(e):
                            # Topic deleted — kill window, unbind, and clean up state
                            w = await tmux_manager.find_window_by_id(wid)
                            if w:
                                await tmux_manager.kill_window(w.window_id)
                            session_manager.unbind_thread(user_id, thread_id)
                            await clear_topic_state(user_id, thread_id, bot)
                            logger.info(
                                "Topic deleted: killed window_id '%s' and "
                                "unbound thread %d for user %d",
                                wid,
                                thread_id,
                                user_id,
                            )
                        else:
                            logger.debug(
                                "Topic probe error for %s: %s",
                                wid,
                                e,
                            )
                    except Exception as e:
                        logger.debug(
                            "Topic probe error for %s: %s",
                            wid,
                            e,
                        )

            for user_id, thread_id, wid in list(session_manager.iter_thread_bindings()):
                try:
                    # Clean up stale bindings (window no longer exists)
                    w = await tmux_manager.find_window_by_id(wid)
                    if not w:
                        # Auto-close topic in project groups
                        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
                        from ..config import config

                        if config.is_conversational_group(
                            chat_id
                        ) or config.is_dev_group(chat_id):
                            try:
                                await bot.delete_forum_topic(
                                    chat_id=chat_id,
                                    message_thread_id=thread_id,
                                )
                                logger.info(
                                    "Auto-deleted topic: chat=%d thread=%d",
                                    chat_id,
                                    thread_id,
                                )
                            except Exception as e:
                                logger.debug("Failed to close topic: %s", e)
                        session_manager.unbind_thread(user_id, thread_id)
                        await clear_topic_state(user_id, thread_id, bot)
                        logger.info(
                            "Cleaned up stale binding: user=%d thread=%d window_id=%s",
                            user_id,
                            thread_id,
                            wid,
                        )
                        continue

                    await update_status_message(
                        bot,
                        user_id,
                        wid,
                        thread_id=thread_id,
                    )
                except Exception as e:
                    logger.debug(
                        f"Status update error for user {user_id} "
                        f"thread {thread_id}: {e}"
                    )

            # Also poll conversational topic_bindings for interactive UI detection
            for topic_chat_id, topic_thread_id, wid in list(
                session_manager.iter_topic_bindings()
            ):
                try:
                    w = await tmux_manager.find_window_by_id(wid)
                    if not w:
                        session_manager.unbind_topic(topic_chat_id, topic_thread_id)
                        logger.info(
                            "Cleaned up stale topic binding: chat=%s thread=%s wid=%s",
                            topic_chat_id,
                            topic_thread_id,
                            wid,
                        )
                        continue

                    # Use chat_id as synthetic user_id for the polling function
                    await update_status_message(
                        bot,
                        topic_chat_id,
                        wid,
                        thread_id=topic_thread_id,
                    )
                except Exception as e:
                    logger.debug(
                        f"Topic status update error for chat {topic_chat_id} "
                        f"thread {topic_thread_id}: {e}"
                    )
        except Exception as e:
            logger.error(f"Status poll loop error: {e}")

        await asyncio.sleep(STATUS_POLL_INTERVAL)
