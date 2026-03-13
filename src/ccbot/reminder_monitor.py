"""Proactive reminder monitor — watches waiting-for.md files per project.

Runs an async background loop that:
  1. Scans each project's work/waiting-for.md on a configurable interval.
  2. Parses items: ``- [ ] [YYYY-MM-DD] [who] — description``
  3. Sends reminders for today-or-past items to the project's Reminders topic.
  4. Provides inline keyboard buttons: +1d, +3d, +1w, Done.
  5. Tracks sent reminders in ~/.ccbot/reminder_state.json to avoid spam.

Key function: start_reminder_loop (called from bot post_init).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup

from .config import config
from .utils import atomic_write_json, ccbot_dir

logger = logging.getLogger(__name__)

# Parse: - [ ] [2026-03-13] [who] — description
# Also matches: - [ ] [2026-03-13] [who] - description (with regular dash)
_ITEM_RE = re.compile(
    r"^-\s+\[\s*(?P<done>[xX ]?)\s*\]\s+"
    r"\[(?P<date>\d{4}-\d{2}-\d{2})\]\s+"
    r"\[(?P<who>[^\]]*)\]\s*"
    r"[—\-]\s*(?P<desc>.+)$"
)

# Callback data prefix for reminder actions
CB_REMIND_PLUS1D = "rem:+1d:"
CB_REMIND_PLUS3D = "rem:+3d:"
CB_REMIND_PLUS1W = "rem:+1w:"
CB_REMIND_DONE = "rem:done:"
CB_REMIND_PREFIX = "rem:"

# Reminders topic name
REMINDERS_TOPIC_NAME = "Reminders"


class ReminderItem:
    """A parsed waiting-for item."""

    __slots__ = ("due_date", "who", "description", "line_number", "done")

    def __init__(
        self, due_date: date, who: str, description: str, line_number: int, done: bool
    ) -> None:
        self.due_date = due_date
        self.who = who
        self.description = description
        self.line_number = line_number
        self.done = done


def parse_waiting_for(text: str) -> list[ReminderItem]:
    """Parse a waiting-for.md file into ReminderItem list."""
    items: list[ReminderItem] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = _ITEM_RE.match(line.strip())
        if not m:
            continue
        done_marker = m.group("done").strip()
        is_done = done_marker.lower() == "x"
        try:
            due = date.fromisoformat(m.group("date"))
        except ValueError:
            continue
        items.append(
            ReminderItem(
                due_date=due,
                who=m.group("who").strip(),
                description=m.group("desc").strip(),
                line_number=i,
                done=is_done,
            )
        )
    return items


def items_due(
    items: list[ReminderItem], as_of: date | None = None
) -> list[ReminderItem]:
    """Filter to items that are due today or overdue (and not done)."""
    today = as_of or date.today()
    return [item for item in items if not item.done and item.due_date <= today]


class ReminderState:
    """Tracks which reminders have been sent to avoid spam."""

    def __init__(self, state_file: Path | None = None) -> None:
        self.state_file = state_file or (ccbot_dir() / "reminder_state.json")
        # Key: "project:line_number:date" → last reminded ISO date
        self._sent: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.state_file.exists():
            try:
                self._sent = json.loads(self.state_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._sent = {}

    def _save(self) -> None:
        atomic_write_json(self.state_file, self._sent)

    def should_remind(self, project: str, item: ReminderItem) -> bool:
        """Check if we should send a reminder for this item today."""
        key = f"{project}:{item.line_number}:{item.due_date.isoformat()}"
        last = self._sent.get(key)
        today_str = date.today().isoformat()
        return last != today_str

    def mark_reminded(self, project: str, item: ReminderItem) -> None:
        """Mark this item as reminded today."""
        key = f"{project}:{item.line_number}:{item.due_date.isoformat()}"
        self._sent[key] = date.today().isoformat()
        self._save()


async def _find_or_create_reminders_topic(
    bot: object, group_chat_id: int
) -> int | None:
    """Find or create a 'Reminders' topic in a group.

    Returns the topic thread_id, or None on failure.
    """
    from telegram import Bot

    assert isinstance(bot, Bot)
    try:
        result = await bot.create_forum_topic(
            chat_id=group_chat_id,
            name=REMINDERS_TOPIC_NAME,
        )
        return result.message_thread_id
    except Exception as e:
        # Topic might already exist — Telegram doesn't have a "list topics" API
        # so we just log the error and return None
        logger.debug("Could not create Reminders topic in %d: %s", group_chat_id, e)
        return None


def build_reminder_keyboard(project: str, item: ReminderItem) -> "InlineKeyboardMarkup":
    """Build inline keyboard for a reminder: reschedule or mark done."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    item_key = f"{item.line_number}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "+1d",
                    callback_data=f"{CB_REMIND_PLUS1D}{project}:{item_key}"[:64],
                ),
                InlineKeyboardButton(
                    "+3d",
                    callback_data=f"{CB_REMIND_PLUS3D}{project}:{item_key}"[:64],
                ),
                InlineKeyboardButton(
                    "+1w",
                    callback_data=f"{CB_REMIND_PLUS1W}{project}:{item_key}"[:64],
                ),
            ],
            [
                InlineKeyboardButton(
                    "✅ Done",
                    callback_data=f"{CB_REMIND_DONE}{project}:{item_key}"[:64],
                ),
            ],
        ]
    )


async def _send_reminders_for_project(
    bot: object,
    project: str,
    group_chat_id: int,
    state: ReminderState,
    reminders_topics: dict[int, int],
) -> None:
    """Check and send reminders for a single project."""
    from telegram import Bot

    assert isinstance(bot, Bot)

    waiting_for_path = config.project_dir(project) / "work" / "waiting-for.md"
    if not waiting_for_path.exists():
        return

    try:
        async with aiofiles.open(waiting_for_path, "r") as f:
            content = await f.read()
    except OSError:
        return

    all_items = parse_waiting_for(content)
    due_items = items_due(all_items)

    if not due_items:
        return

    # Filter to items we haven't reminded about today
    to_remind = [item for item in due_items if state.should_remind(project, item)]
    if not to_remind:
        return

    # Get or create Reminders topic
    topic_id = reminders_topics.get(group_chat_id)
    if topic_id is None:
        topic_id = await _find_or_create_reminders_topic(bot, group_chat_id)
        if topic_id is not None:
            reminders_topics[group_chat_id] = topic_id

    for item in to_remind:
        overdue = (date.today() - item.due_date).days
        overdue_str = f" ({overdue}d overdue)" if overdue > 0 else ""

        text = (
            f"⏰ **{project}**: {item.description}\n"
            f"Due: {item.due_date.isoformat()}{overdue_str}\n"
            f"Who: {item.who}"
        )

        keyboard = build_reminder_keyboard(project, item)

        try:
            await bot.send_message(
                chat_id=group_chat_id,
                text=text,
                message_thread_id=topic_id,
                reply_markup=keyboard,
            )
            state.mark_reminded(project, item)
            logger.info(
                "Sent reminder: project=%s, item='%s', due=%s",
                project,
                item.description[:40],
                item.due_date,
            )
        except Exception as e:
            logger.error("Failed to send reminder: %s", e)


async def _reminder_loop(bot: object) -> None:
    """Background loop that checks waiting-for.md files periodically."""
    state = ReminderState()
    # Cache Reminders topic IDs: group_chat_id → thread_id
    reminders_topics: dict[int, int] = {}

    # Run immediately on startup, then at interval
    first_run = True
    while True:
        if not first_run:
            await asyncio.sleep(config.reminder_interval)
        first_run = False

        logger.debug("Reminder check starting")
        for project, group_chat_id in config.project_groups.items():
            try:
                await _send_reminders_for_project(
                    bot, project, group_chat_id, state, reminders_topics
                )
            except Exception as e:
                logger.error("Reminder check failed for %s: %s", project, e)


def start_reminder_loop(bot: object) -> asyncio.Task[None]:
    """Start the background reminder monitoring task."""
    return asyncio.create_task(_reminder_loop(bot))


async def handle_reminder_callback(bot: object, data: str, query: object) -> bool:
    """Handle reminder callback data (reschedule / done).

    Returns True if handled, False if not a reminder callback.
    """
    if not data.startswith(CB_REMIND_PREFIX):
        return False

    from telegram import Bot, CallbackQuery

    assert isinstance(bot, Bot)
    assert isinstance(query, CallbackQuery)

    # Parse: rem:+1d:project:line_number or rem:done:project:line_number
    parts = data.split(":")
    if len(parts) < 4:
        await query.answer("Invalid reminder data")
        return True

    action = parts[1]
    project = parts[2]
    try:
        line_number = int(parts[3])
    except ValueError:
        await query.answer("Invalid reminder data")
        return True

    # Validate project is a known project (prevent path traversal)
    if project not in config.project_groups:
        await query.answer("Unknown project")
        return True

    waiting_for_path = config.project_dir(project) / "work" / "waiting-for.md"
    # Double-check resolved path is under projects_dir
    try:
        resolved = waiting_for_path.resolve()
        if not str(resolved).startswith(str(config.projects_dir.resolve())):
            await query.answer("Invalid project path")
            return True
    except (OSError, ValueError):
        await query.answer("Invalid project path")
        return True
    if not waiting_for_path.exists():
        await query.answer("File not found")
        return True

    try:
        async with aiofiles.open(waiting_for_path, "r") as f:
            lines = await f.readlines()
    except OSError:
        await query.answer("Error reading file")
        return True

    if line_number < 1 or line_number > len(lines):
        await query.answer("Line not found")
        return True

    line = lines[line_number - 1]
    m = _ITEM_RE.match(line.strip())
    if not m:
        await query.answer("Item format changed")
        return True

    current_date = date.fromisoformat(m.group("date"))

    if action == "done":
        # Mark as done: replace [ ] with [x]
        new_line = line.replace("[ ]", "[x]", 1)
        lines[line_number - 1] = new_line
        feedback = "✅ Marked done"
    elif action in ("+1d", "+3d", "+1w"):
        delta_map = {"+1d": 1, "+3d": 3, "+1w": 7}
        new_date = current_date + timedelta(days=delta_map[action])
        new_line = line.replace(
            f"[{current_date.isoformat()}]",
            f"[{new_date.isoformat()}]",
            1,
        )
        lines[line_number - 1] = new_line
        feedback = f"📅 Rescheduled to {new_date.isoformat()}"
    else:
        await query.answer("Unknown action")
        return True

    # Write back atomically (temp file + rename)
    import os
    import tempfile

    try:
        content = "".join(lines)
        fd, tmp_path = tempfile.mkstemp(dir=str(waiting_for_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, str(waiting_for_path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.error("Error writing %s: %s", waiting_for_path, e)
        await query.answer("Error writing file")
        return True

    # Update the message to show action taken
    try:
        msg = query.message
        original_text = getattr(msg, "text", "") if msg else ""
        await query.edit_message_text(
            text=f"~~{original_text}~~\n\n{feedback}",
        )
    except Exception:
        pass

    await query.answer(feedback)
    return True
