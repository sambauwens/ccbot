"""Dev group session sync -- auto-create/close topics for tmux sessions.

Handles bidirectional tmux <-> dev topic synchronization:
  - handle_new_session: Auto-create a dev topic when a new tmux session starts
  - reconcile_dev_topics: Startup reconciliation for existing sessions without topics
  - handle_session_removed: Close dev topic and send merge reminder when session ends
"""

import logging

from telegram import Bot

from ..config import config
from ..session import session_manager
from ..session_monitor import NewSession
from .cleanup import clear_topic_state
from .message_sender import safe_send

logger = logging.getLogger(__name__)


async def handle_new_session(session: NewSession, bot: Bot) -> None:
    """Auto-create a Telegram topic for a new session in the dev group.

    Phase 3: bidirectional tmux <-> dev topic sync. For now, creates dev topics
    in the dev group for new tmux sessions. Conversational groups are skipped.
    """
    from ..bot import _topic_names

    # Check if this window is already bound (thread_bindings or topic_bindings)
    for _, _, bound_wid in session_manager.iter_thread_bindings():
        if bound_wid == session.window_id:
            return
    for _, _, bound_wid in session_manager.iter_topic_bindings():
        if bound_wid == session.window_id:
            return

    # Dev group sync: create a dev topic for this tmux session
    chat_id = config.dev_group
    if not chat_id:
        return

    # Derive topic name from tmux session name
    topic_title = session.tmux_session_name or session.window_name
    if not topic_title:
        topic_title = "New session"

    logger.info(
        "Auto-creating dev topic '%s' for session %s (chat=%d)",
        topic_title,
        session.window_id,
        chat_id,
    )

    try:
        forum_topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_title)
        new_thread_id = forum_topic.message_thread_id
    except Exception as e:
        logger.error(
            "Failed to auto-create topic for session %s: %s", session.window_id, e
        )
        return

    # Bind for DEV_USERS only (dev group is Sam-only)
    for user_id in config.dev_users:
        session_manager.set_group_chat_id(user_id, new_thread_id, chat_id)
        session_manager.bind_thread(
            user_id,
            new_thread_id,
            session.window_id,
            window_name=session.window_name,
        )

    _topic_names[(chat_id, new_thread_id)] = topic_title
    logger.info(
        "Auto-created dev topic '%s' (thread=%d) for session %s",
        topic_title,
        new_thread_id,
        session.window_id,
    )


async def reconcile_dev_topics(bot: Bot) -> None:
    """Startup reconciliation: create dev topics for tmux sessions without topics.

    Reads session_map.json to find all active windows, checks which ones
    are already bound, and creates dev topics for unbound ones.
    """
    from ..bot import _topic_names

    if not config.dev_group:
        return

    session_map = session_manager.read_session_map_sync()
    if not session_map:
        return

    # Collect all bound window IDs
    bound_ids: set[str] = set()
    for _, _, wid in session_manager.iter_thread_bindings():
        bound_ids.add(wid)
    for _, _, wid in session_manager.iter_topic_bindings():
        bound_ids.add(wid)

    created = 0
    for map_key, info in session_map.items():
        # map_key format: "tmux_session_name:@window_id"
        if ":" not in map_key:
            continue
        tmux_session_name, window_id = map_key.rsplit(":", 1)
        if window_id in bound_ids:
            continue

        # Use tmux session name as topic title
        topic_title = tmux_session_name or info.get("window_name", "Session")

        try:
            forum_topic = await bot.create_forum_topic(
                chat_id=config.dev_group, name=topic_title
            )
            new_thread_id = forum_topic.message_thread_id

            for user_id in config.dev_users:
                session_manager.set_group_chat_id(
                    user_id, new_thread_id, config.dev_group
                )
                session_manager.bind_thread(
                    user_id,
                    new_thread_id,
                    window_id,
                    window_name=info.get("window_name", ""),
                )

            _topic_names[(config.dev_group, new_thread_id)] = topic_title
            bound_ids.add(window_id)
            created += 1
            logger.info(
                "Reconciled: created dev topic '%s' (thread=%d) for %s",
                topic_title,
                new_thread_id,
                window_id,
            )
        except Exception as e:
            logger.error("Failed to create dev topic for %s: %s", window_id, e)

    if created:
        logger.info("Reconciliation: created %d dev topics", created)


async def handle_session_removed(window_id: str, bot: Bot) -> None:
    """Handle a tmux session being killed -- close the dev topic and send merge reminder."""
    from ..bot import _worktree_sources

    if not config.dev_group:
        return

    # Get the display name (worktree name) before unbinding
    display_name = session_manager.get_display_name(window_id)

    # Find thread_bindings pointing to this window in the dev group
    for user_id, thread_id, wid in list(session_manager.iter_thread_bindings()):
        if wid != window_id:
            continue
        chat_id = session_manager.resolve_chat_id(user_id, thread_id)
        if chat_id != config.dev_group:
            continue

        # Close the topic in the dev group
        try:
            await bot.close_forum_topic(
                chat_id=config.dev_group, message_thread_id=thread_id
            )
            logger.info(
                "Closed dev topic (thread=%d) for removed window %s",
                thread_id,
                window_id,
            )
        except Exception as e:
            logger.debug("Failed to close dev topic: %s", e)

        session_manager.unbind_thread(user_id, thread_id)
        await clear_topic_state(user_id, thread_id, bot)

    # Send merge reminder to source conversational topic if this was a worktree session
    source = _worktree_sources.pop(display_name, None)
    if source:
        source_chat_id, source_thread_id = source
        try:
            await safe_send(
                bot,
                source_chat_id,
                f"Dev session `{display_name}` ended.\n"
                f"Run `$merge {display_name}` to merge to main.",
                message_thread_id=source_thread_id,
            )
        except Exception as e:
            logger.debug("Failed to send merge reminder: %s", e)
