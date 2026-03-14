"""Topic lifecycle handlers -- created, closed, edited events.

Handles Telegram forum topic status changes:
  - topic_created_handler: Cache topic names from FORUM_TOPIC_CREATED
  - topic_closed_handler: Kill associated tmux window and clean up state
  - topic_edited_handler: Sync renamed topic to tmux window and internal state
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..config import config
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .cleanup import clear_topic_state

logger = logging.getLogger(__name__)


def _resolve_project_for_chat(chat: object) -> str | None:
    """Resolve a project name from a Telegram chat (group) via PROJECT_GROUPS config."""
    if chat is None:
        return None
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return None
    return config.project_for_group(chat_id)


async def topic_closed_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle topic closure -- kill the associated tmux window and clean up state."""
    from ..bot import is_user_allowed, _get_thread_id

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        return

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if wid:
        display = session_manager.get_display_name(wid)
        w = await tmux_manager.find_window_by_id(wid)
        if w:
            await tmux_manager.kill_window(w.window_id)
            logger.info(
                "Topic closed: killed window %s (user=%d, thread=%d)",
                display,
                user.id,
                thread_id,
            )
        else:
            logger.info(
                "Topic closed: window %s already gone (user=%d, thread=%d)",
                display,
                user.id,
                thread_id,
            )
        session_manager.unbind_thread(user.id, thread_id)
        # Clean up all memory state for this topic
        await clear_topic_state(user.id, thread_id, context.bot, context.user_data)
    else:
        logger.debug(
            "Topic closed: no binding (user=%d, thread=%d)", user.id, thread_id
        )


async def topic_created_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cache topic names from FORUM_TOPIC_CREATED service messages."""
    from ..bot import _topic_names

    msg = update.message
    if not msg or not msg.forum_topic_created:
        return
    chat = update.effective_chat
    thread_id = getattr(msg, "message_thread_id", None)
    if chat and thread_id:
        _topic_names[(chat.id, thread_id)] = msg.forum_topic_created.name
        logger.debug(
            "Cached topic name: chat=%d thread=%d name='%s'",
            chat.id,
            thread_id,
            msg.forum_topic_created.name,
        )


async def topic_edited_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle topic rename -- sync new name to tmux window and internal state."""
    from ..bot import is_user_allowed, _get_thread_id, _topic_names, _sanitize_tmux_name

    user = update.effective_user
    if not user or not is_user_allowed(user.id):
        return

    msg = update.message
    if not msg or not msg.forum_topic_edited:
        return

    new_name = msg.forum_topic_edited.name
    if new_name is None:
        # Icon-only change, no rename needed
        return

    thread_id = _get_thread_id(update)
    if thread_id is None:
        return

    # Update topic name cache
    chat = update.effective_chat
    if chat and thread_id:
        _topic_names[(chat.id, thread_id)] = new_name

    wid = session_manager.get_window_for_thread(user.id, thread_id)
    if not wid:
        logger.debug(
            "Topic edited: no binding (user=%d, thread=%d)", user.id, thread_id
        )
        return

    # For project groups, prefix the project name to tmux session name
    tmux_name = new_name
    project_name = _resolve_project_for_chat(chat)
    if project_name:
        tmux_name = f"{project_name}-{_sanitize_tmux_name(new_name)}"

    old_name = session_manager.get_display_name(wid)
    await tmux_manager.rename_window(wid, tmux_name)
    session_manager.update_display_name(wid, tmux_name)
    logger.info(
        "Topic renamed: '%s' -> '%s' (tmux='%s', window=%s, user=%d, thread=%d)",
        old_name,
        new_name,
        tmux_name,
        wid,
        user.id,
        thread_id,
    )
