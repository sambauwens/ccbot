# Architectural Analysis: Why Conversational Topics Keep Breaking

Research from session analysis agent (2026-03-15).

## Five Fundamental Issues

### 1. Session instruction sent as fake user message

The instruction ("You are in a conversational Telegram topic...") is sent as tmux keystrokes — Claude sees it as a user message. Problems:
- Claude responds to it (despite "do not respond")
- It consumes a conversation turn, confusing context with --resume
- The instruction has zero enforcement power — `--allowedTools` is what enforces permissions
- The instruction text gets forwarded to Telegram when the offset advancement fails

### 2. Sleep-and-hope offset advancement

```python
await asyncio.sleep(2.0)   # hope Claude started
# send instruction
await asyncio.sleep(5.0)   # hope Claude processed it
file_size = Path(session.file_path).stat().st_size
# set offset to skip instruction exchange
```

Race condition by design:
- If Claude hasn't finished in 5s → offset too early → instruction response forwarded to topic
- If monitor polls between instruction send and offset set → instruction exchange forwarded
- If JSONL doesn't exist yet → session.file_path is None → offset never set

### 3. Handler blocks for 7-22 seconds

Session creation path: `wait_for_session_map_entry` (up to 15s) + `sleep(2)` + `sleep(5)` = 7-22s blocking. During this time:
- User gets no feedback
- Other messages queued
- Telegram webhook may timeout (30s)

### 4. Shared delivery path with wrong abstractions

Message queue keyed by `user_id` for dev topics, `chat_id` (negative) for conversational. Status messages, tool message tracking, interactive UI tracking all use `(user_id, thread_id)` tuples — mixing the two models creates collisions.

### 5. "Higher-order broker" implemented as 1:1 session binding

Requirements say conversational topics should be "beyond a single session" and the bot should be a "broker." Implementation directly binds one topic to one tmux window. The $plan flow kills and recreates the session, the auto-restart recurses through the entire creation+instruction+offset sequence.

## Root Cause

The conversational handler tries to be both a session lifecycle manager AND a message router in the same synchronous handler path. These concerns need to be separated.

## Proposed Fix

### A. Remove instruction-as-user-message entirely

`--allowedTools` already enforces read-only. Behavioral instructions ("suggest $plan") belong in the project's CLAUDE.md or via `--append-system-prompt` flag — NOT as fake user messages via tmux keystrokes.

### B. Replace sleep-based offset with deterministic filtering

Instead of sleeping and hoping, either:
1. Don't send instructions at all (use `--append-system-prompt` instead)
2. Filter by content: mark instruction exchanges with a sentinel and filter in the delivery layer
3. Track a "skip_until" offset that is updated when the monitor sees the instruction's stop_reason

### C. Make session creation non-blocking

1. Immediately reply "Creating session..."
2. Kick off creation as background task
3. Queue user's message to send once session ready
4. Return immediately

### D. Use `--append-system-prompt` for behavioral instructions

Claude Code supports `--append-system-prompt <prompt>` which injects text into the system prompt without creating a user message turn. This eliminates problems 1, 2, and 3 entirely for the instruction use case.

### E. Don't mix conversational and dev delivery in the same message handler

Conversational topics need their own delivery path that:
- Filters user messages from Telegram (already done)
- Filters the instruction exchange (currently broken)
- Delivers to topic (not per-user)
- Handles interactive UIs with chat_id (not user_id)
