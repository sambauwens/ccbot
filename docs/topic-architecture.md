# Topic Architecture

The bot supports two topic types across two group types.

## Two Topic Types

**Conversational topics** (human-created, multi-user):
- Bound via `topic_bindings` (per-topic, not per-user)
- Claude Code session with `--dangerously-skip-permissions` in project directory
- Any user's message routes to the same session (prefixed with sender name)
- Delivery: single message to the topic

**Dev session topics** (bot-created, 1:1 with tmux):
- Bound via `thread_bindings` (per-user)
- Direct session interaction
- Delivery: per-user

## Two Group Types

| | Conversational Groups | Dev Group |
|---|---|---|
| Config | `CONVERSATIONAL_GROUPS` | `DEV_GROUP` |
| Members | All ALLOWED_USERS | DEV_USERS only |
| User-created topics | Conversational | Conversational (configurable) |
| Bot-created topics | — | Dev session (tmux sync) |

## Binding Models

### Conversational: topic_bindings

```python
# session.py: SessionManager
topic_bindings: dict[str, str]   # "chat_id:thread_id" → window_id
topic_types: dict[str, str]      # "chat_id:thread_id" → "conversational" | "dev"
```

- Storage: memory + `state.json`
- Written when: first message in an unbound topic in a managed group
- Purpose: route any user's message to the shared session
- General topic: thread_id=None maps to key `"chat_id:0"`

### Dev sessions: thread_bindings (existing)

```python
thread_bindings: dict[int, dict[int, str]]  # user_id → {thread_id → window_id}
window_display_names: dict[str, str]        # window_id → window_name (for display)
```

- Storage: memory + `state.json`
- Written when: dev session auto-created from tmux sync or plan acceptance
- Purpose: route user messages to the correct tmux window

## Mapping 2: Window ID → Session (session_map.json)

```python
# session_map.json (key format: "tmux_session:window_id")
{
  "ccbot:@0": {"session_id": "uuid-xxx", "cwd": "/path/to/project", "window_name": "project"},
  "ccbot:@5": {"session_id": "uuid-yyy", "cwd": "/path/to/project", "window_name": "project-2"}
}
```

- Storage: `session_map.json`
- Written when: Claude Code's `SessionStart` hook fires
- Property: one window maps to one session; session_id changes after `/clear`
- Purpose: SessionMonitor uses this mapping to decide which sessions to watch

## Message Flows

### Conversational topic flow:
```
User sends "hello" in france-2026 topic (chat_id=-100xxx, thread_id=42)
  → topic_bindings["-100xxx:42"] → "@5"
  → send_to_window("@5", "[Sam] hello")  # prefixed with sender name
```

### Conversational inbound:
```
SessionMonitor reads new message (session_id = "uuid-xxx")
  → find_topics_for_session() → [(-100xxx, 42, "@5")]
  → Deliver once to topic (chat_id=-100xxx, thread_id=42)
```

### Dev session flow:
```
User sends "hello" in dev topic (thread_id=5)
  → thread_bindings[user_id][5] → "@0"
  → send_to_window("@0", "hello")
```

**New conversational topic**: First message in unbound topic in managed group → auto-create Claude Code session in project directory → bind via topic_bindings → forward message.

**New dev topic**: tmux session created externally → bot detects via session_map → auto-creates topic in dev group → binds via thread_bindings.

**Topic lifecycle**: Closing a topic kills the associated tmux window. Stale bindings cleaned up by status polling.

## $ Commands (conversational topics)

| Command | Who | Description |
|---|---|---|
| `$plan [context]` | Any user | Instructs Claude to enter planning mode |
| `$accept <name>` | DEV_USERS | Creates worktree, writes plan, spawns dev session |
| `$merge <worktree>` | DEV_USERS | Merges worktree branch to main, releases worktree |
| `$new [title]` | Any user | Creates a new conversational topic, carries context |

### Planning flow:
```
$plan → Claude explores + writes plan → back-and-forth Q&A
  → $accept add-login → creates france-2026-add-login-ws worktree
  → spawns dev session in dev group → link posted in conversational topic
  → dev work happens in the worktree
  → $merge france-2026-add-login-ws → merges to main, releases worktree
```

## Session Lifecycle

**Startup cleanup**: On bot startup, all tracked sessions not present in session_map are cleaned up, preventing monitoring of closed sessions.

**Runtime change detection**: Each polling cycle checks for session_map changes:
- Window's session_id changed (e.g., after `/clear`) → clean up old session
- Window deleted → clean up corresponding session
