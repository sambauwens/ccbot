"""Tests for reminder monitor — parsing, filtering, and state tracking."""

from datetime import date

from ccbot.reminder_monitor import (
    ReminderItem,
    items_due,
    parse_waiting_for,
)


class TestParseWaitingFor:
    def test_basic_item(self):
        text = "- [ ] [2026-03-13] [Sam] — Call school about enrollment"
        items = parse_waiting_for(text)
        assert len(items) == 1
        assert items[0].due_date == date(2026, 3, 13)
        assert items[0].who == "Sam"
        assert items[0].description == "Call school about enrollment"
        assert items[0].done is False

    def test_done_item(self):
        text = "- [x] [2026-03-10] [Sam] — Submitted application"
        items = parse_waiting_for(text)
        assert len(items) == 1
        assert items[0].done is True

    def test_done_item_uppercase_x(self):
        text = "- [X] [2026-03-10] [Nathalie] — Check email"
        items = parse_waiting_for(text)
        assert len(items) == 1
        assert items[0].done is True

    def test_regular_dash_separator(self):
        text = "- [ ] [2026-03-13] [Sam] - Call school"
        items = parse_waiting_for(text)
        assert len(items) == 1
        assert items[0].description == "Call school"

    def test_em_dash_separator(self):
        text = "- [ ] [2026-03-13] [Sam] — Call school"
        items = parse_waiting_for(text)
        assert len(items) == 1
        assert items[0].description == "Call school"

    def test_multiple_items(self):
        text = (
            "# Waiting For\n"
            "\n"
            "- [ ] [2026-03-13] [Sam] — Call school\n"
            "- [x] [2026-03-10] [Nathalie] — Check email\n"
            "- [ ] [2026-03-15] [Sam] — Submit docs\n"
        )
        items = parse_waiting_for(text)
        assert len(items) == 3

    def test_malformed_date(self):
        text = "- [ ] [not-a-date] [Sam] — Something"
        items = parse_waiting_for(text)
        assert len(items) == 0

    def test_non_item_lines_ignored(self):
        text = (
            "# Waiting For\n"
            "\n"
            "Some intro text.\n"
            "- [ ] [2026-03-13] [Sam] — Call school\n"
            "- Regular list item\n"
        )
        items = parse_waiting_for(text)
        assert len(items) == 1

    def test_empty_file(self):
        items = parse_waiting_for("")
        assert len(items) == 0

    def test_line_numbers_correct(self):
        text = (
            "# Header\n"
            "\n"
            "- [ ] [2026-03-13] [Sam] — First item\n"
            "- [ ] [2026-03-14] [Sam] — Second item\n"
        )
        items = parse_waiting_for(text)
        assert items[0].line_number == 3
        assert items[1].line_number == 4


class TestItemsDue:
    def test_today_item_is_due(self):
        item = ReminderItem(
            due_date=date(2026, 3, 13),
            who="Sam",
            description="Test",
            line_number=1,
            done=False,
        )
        result = items_due([item], as_of=date(2026, 3, 13))
        assert len(result) == 1

    def test_past_item_is_due(self):
        item = ReminderItem(
            due_date=date(2026, 3, 10),
            who="Sam",
            description="Overdue",
            line_number=1,
            done=False,
        )
        result = items_due([item], as_of=date(2026, 3, 13))
        assert len(result) == 1

    def test_future_item_not_due(self):
        item = ReminderItem(
            due_date=date(2026, 3, 15),
            who="Sam",
            description="Not yet",
            line_number=1,
            done=False,
        )
        result = items_due([item], as_of=date(2026, 3, 13))
        assert len(result) == 0

    def test_done_item_not_due(self):
        item = ReminderItem(
            due_date=date(2026, 3, 13),
            who="Sam",
            description="Done",
            line_number=1,
            done=True,
        )
        result = items_due([item], as_of=date(2026, 3, 13))
        assert len(result) == 0

    def test_mixed_items(self):
        items = [
            ReminderItem(date(2026, 3, 10), "Sam", "Overdue", 1, False),
            ReminderItem(date(2026, 3, 13), "Sam", "Today", 2, False),
            ReminderItem(date(2026, 3, 15), "Sam", "Future", 3, False),
            ReminderItem(date(2026, 3, 10), "Sam", "Done overdue", 4, True),
        ]
        result = items_due(items, as_of=date(2026, 3, 13))
        assert len(result) == 2
        assert result[0].description == "Overdue"
        assert result[1].description == "Today"
