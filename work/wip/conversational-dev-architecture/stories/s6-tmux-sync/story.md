# S6: Tmux Bidirectional Sync

## Context

From the planning discussion (Message 4, 12:06 UTC):

> the dev group starts with a "General" conversational topic, and then there is a dev session for every open tmux "window", and it keeps it automatically mirrored (if I create a window from somewhere else or when a dev topic is created from the bot/telegram side).

From AskUserQuestion #8:

> when a tmux session dies than the synced dev topic is indeed closed (archived I suppose? I don't know what archiving leads to?). And yeah it's full bidirectional sync, there should always be a topic dev session for every tmux session and vice versa.

## Current Implementation

### Tmux → Telegram
- `handle_new_session` in `dev_session_sync.py` detects new windows via `session_map.json` changes
- Auto-creates dev topic in dev group, binds for DEV_USERS
- Topic name derived from tmux session name

### Telegram → Tmux
- Dev topic created from `$accept` spawns a new tmux session
- Directory browser flow (for non-managed groups) also creates sessions

### Cleanup
- `handle_session_removed` — when tmux session dies, closes the dev topic
- `topic_closed_handler` — when topic is closed, kills the tmux session
- Status polling detects stale bindings and cleans up

### Startup reconciliation
- `reconcile_dev_topics` runs at bot startup
- Creates dev topics for all tmux sessions in session_map that lack topics
- Reads session_map.json synchronously for the initial pass

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | New tmux sessions → auto-create dev topic | done |
| T2 | Topic close → kill tmux session | done (existing) |
| T3 | Tmux session kill → close dev topic | done |
| T4 | Startup reconciliation | done |
| T5 | Dev group General topic exists at startup | done (created during migration) |

## Acceptance Criteria

- Every tmux session has a corresponding dev topic in the dev group
- Creating a tmux session from terminal auto-creates a dev topic
- Closing a dev topic kills the tmux session
- Killing a tmux session closes the dev topic
- On bot restart, missing topics are created for existing sessions
