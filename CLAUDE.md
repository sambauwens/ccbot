# CLAUDE.md

ccbot — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux. Each topic is bound to one tmux session running one Claude Code instance. One Telegram supergroup per project, sessions discoverable by `dev go`.

Tech stack: Python, python-telegram-bot, tmux, uv.

## Common Commands

```bash
uv run ruff check src/ tests/         # Lint — MUST pass before committing
uv run ruff format src/ tests/        # Format — auto-fix, then verify with --check
uv run pyright src/ccbot/             # Type check — MUST be 0 errors before committing
dev bot restart                       # Restart after code changes — ALWAYS do this
ccbot hook --install                  # Auto-install Claude Code SessionStart hook
```

After modifying any source file under `src/ccbot/`, you MUST run `dev bot restart` to apply changes.

## Core Design Constraints

- **Session-per-instance** — each Claude instance gets its own tmux session (not a window inside a shared session). Sessions are discoverable by `dev go` and attachable from the terminal.
- **1 Topic = 1 Session** — all internal routing keyed by tmux window ID (`@0`, `@12`), globally unique across sessions. Window names kept as display names.
- **Multi-group project routing** — one Telegram supergroup per project. `PROJECT_GROUPS` env var maps project names to group chat IDs. Unbound topics in a project group auto-create sessions in the project directory.
- **Topic-only** — no backward-compat for non-topic mode. No `active_sessions`, no `/list`, no General topic routing.
- **No message truncation** at parse layer — splitting only at send layer (`split_message`, 4096 char limit).
- **MarkdownV2 only** — use `safe_reply`/`safe_edit`/`safe_send` helpers (auto fallback to plain text). Internal queue/UI code calls bot API directly with its own fallback.
- **Hook-based session tracking** — `SessionStart` hook writes `session_map.json`; monitor polls it to detect session changes.
- **Message queue per user** — FIFO ordering, message merging (3800 char limit), tool_use/tool_result pairing.
- **Rate limiting** — `AIORateLimiter(max_retries=5)` on the Application (30/s global). On restart, the global bucket is pre-filled to avoid burst against Telegram's server-side counter.
- **Proactive reminders** — background monitor reads `work/waiting-for.md` per project, sends reminders to the Reminders topic with reschedule/done buttons.

## Code Conventions

- Every `.py` file starts with a module-level docstring: purpose clear within 10 lines, one-sentence summary first line, then core responsibilities and key components.
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes; use `answer_callback_query` for instant feedback.

## Configuration

- Config directory: `~/.ccbot/` by default, override with `CCBOT_DIR` env var.
- `.env` loading priority: local `.env` > config dir `.env`.
- State files: `state.json` (thread bindings), `session_map.json` (hook-generated), `monitor_state.json` (byte offsets), `reminder_state.json` (reminder tracking).
- `PROJECT_GROUPS` — JSON mapping project name → Telegram group chat ID. E.g. `{"france-2026": -100123, "outstanding": -100456}`
- `CCBOT_PROJECTS_DIR` — base directory for projects (default `~/dev/@active`)
- `REMINDER_INTERVAL` — seconds between reminder checks (default: 21600 = 6h)

## Hook Configuration

Auto-install: `ccbot hook --install`

Or manually in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ]
  }
}
```

## Architecture Details

See @.claude/references/architecture.md for full system diagram and module inventory.
See @.claude/references/topic-architecture.md for topic→window→session mapping details.
See @.claude/references/message-handling.md for message queue, merging, and rate limiting.
