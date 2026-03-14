# Conversational + Dev Topic Architecture

## Context

ccbot is currently developer-only: every topic is a direct pipe to a Claude Code session. Two goals drive this redesign:

1. **Collaboration with non-developers** (Nathalie for france-2026, others later) — they need to participate in project discussions, research, and planning through Telegram, where Claude helps research and plan, and Sam approves before code changes happen.

2. **Safety through workflow** — instead of relying on Claude Code's permission flags, safety comes from separating planning from implementation. A conversational session can read anything and write plans, but code changes only happen after plan acceptance in a dedicated work session running in its own worktree.

Sam primarily uses terminal (via moshi) for dev work. Telegram dev topics complement the terminal experience: image/file sharing, long messages, and planning mode Q&A.

## Architecture Overview

### Two Group Types

| | Conversational Groups | Dev Group |
|---|---|---|
| Count | Per-project (france-2026, etc.) | One group, all projects |
| Members | Sam + collaborators (Nathalie) | Sam only |
| User-created topics | Conversational | Conversational (configurable, likely to change) |
| Bot-created topics | — | Dev session topics (synced with tmux) |

### Two Topic Types

**Conversational topic** (human-created, higher-order):
- Created when any user creates a topic, or via `$new` from General
- Claude Code session with `--dangerously-skip-permissions` in main branch worktree
- All users' messages go to the same session (multi-user)
- Can be just Q&A, research, discussion — not everything leads to planning
- Claude is instructed to suggest `$plan` when conversation leads toward code changes
- `$plan` starts explicit planning; `$accept` (Sam only) finalizes → worktree + dev session
- GitHub links to referenced files so collaborators can read them
- Conversational only: no status lines, no tool output, just text + interactive UIs

**Dev session topic** (bot-created only, 1:1 with tmux session):
- Created automatically by the bot: either from plan acceptance or tmux session sync
- Full bidirectional sync: tmux session ↔ dev topic (create/close either side)
- Sam only (dev group)
- Direct session interaction (input channel for terminal work)
- No `--dangerously-skip-permissions` by default
- `$mode` still available for permission switching

### General Topic (in any group)

- Lightweight conversational topic (bot responds to messages directly)
- Bot posts reminders and initiated communications here
- In conversational groups: the only topic after migration (user creates new ones as needed)
- In dev group: exists alongside auto-synced dev session topics

### Subject Change Detection (all conversational topics)

- Bot detects when conversation drifts to a new subject (using Claude AI classification)
- When detected, suggests: `$new <suggested-topic-title>` — with a pre-suggested title
- Applies to ANY conversational topic (not just General)
- Not on every message — only when the bot detects a genuine subject shift

### User Roles

- `ALLOWED_USERS` → all users (Sam + Nathalie + future collaborators)
- `DEV_USERS` → users who can use dev group and `$accept` plans (Sam)
- Non-dev users: conversational groups only

## Detailed Changes

### 1. Group Configuration

**`config.py`**:

```python
# Conversational groups: project_name → chat_id
CONVERSATIONAL_GROUPS = {"france-2026": -100123}

# Dev group: single chat_id
DEV_GROUP = -100456

# Whether user-created topics in dev group are conversational (configurable)
DEV_GROUP_USER_TOPICS = "conversational"  # or "dev" later

# User roles
DEV_USERS = {123456}  # subset of ALLOWED_USERS who can $accept and use dev group
```

### 2. Topic Binding Model

**`session.py`** — add topic-level bindings alongside existing per-user bindings:

```python
# Existing (dev topics): per-user
thread_bindings: dict[int, dict[int, str]]  # user_id → {thread_id → window_id}

# New (conversational topics): per-topic
topic_bindings: dict[str, str]  # "chat_id:thread_id" → window_id
topic_types: dict[str, str]     # "chat_id:thread_id" → "conversational" | "dev"
```

- Conversational topics: `topic_bindings` (any user's message routes to the window)
- Dev topics: `thread_bindings` (per-user, existing behavior)
- Message delivery: for `topic_bindings`, send once to the topic (not per-user)

### 3. Conversational Topic Flow

**Creation** (user creates topic in any group, or `$new` in General):
1. User creates topic or sends first message in new topic
2. Determine project from group config
3. Find main branch worktree: `<project>/<project>-main/`
4. Launch Claude Code: `claude --dangerously-skip-permissions` in that directory
5. Bind via `topic_bindings`
6. Claude session instructions include: suggest `$plan` when changes seem needed
7. Forward the user's message

**Multi-user messaging**:
- All messages from any ALLOWED_USER go to the same session
- Prefix with sender name: `[Nathalie] What about...`
- Delivery: single message to the topic (visible to all members)

**Q&A vs Planning**:
- Most conversations are just Q&A — no special handling needed
- Claude suggests `$plan` when it detects the conversation leads toward code changes
- User can always explicitly run `$plan` to start planning

**GitHub links**:
- Extract git remote URL from worktree
- Post-process Claude's responses to add GitHub links for referenced files
- Link directly to the matching header anchor when one exists (e.g., `#architecture-overview`)
- Format: `[filename#heading](https://github.com/owner/repo/blob/main/path/to/file#heading)`
- Focus on .md files and key referenced files

### 4. `$plan` and `$accept` Flow

**`$plan`** (any user in conversational topic):
- Sends a message to the Claude session instructing it to enter planning mode
- Claude explores, researches, writes plan files
- Back-and-forth Q&A with users to refine the plan

**`$accept`** (Sam only):
1. Ask conversational Claude to produce a structured plan document (epic/story/task)
2. Create worktree:
   - Read `.workspace-pool.yml` for the project
   - `git worktree add` from bare repo
   - Name: `<project>-<plan-name>-ws`
   - Update pool file (status: reserved, agent: claude)
3. Write plan files in worktree (epic, stories, tasks, research context)
4. Spawn dev session:
   - Create dev topic in dev group
   - Launch Claude Code in the worktree (no --dangerously-skip-permissions)
   - Initial prompt: the plan
5. Notify conversational topic: "Plan accepted. Work session created: [topic link]"

### 5. Dev Session ↔ Tmux Bidirectional Sync

**Tmux → Telegram** (session created externally, e.g., via `dev go`):
- Bot polls/detects new tmux sessions (via session_map.json or tmux list)
- Auto-creates dev topic in dev group
- Topic name derived from tmux session name
- Binds via `thread_bindings` (Sam only)

**Telegram → Tmux** (dev topic created from plan acceptance):
- Already creates tmux session (existing behavior)

**Cleanup sync**:
- Tmux session killed → bot closes/archives the dev topic
- Dev topic closed → bot kills the tmux session (existing behavior)

**Always in sync**: on bot startup, reconcile: create topics for tmux sessions without topics, clean up topics for dead sessions.

### 6. Dev Topic Changes

- Remove `--dangerously-skip-permissions` default from `tmux_manager.py`
- Keep `$mode` for manual permission switching
- Planning mode UX: format Q&A exchanges cleanly for phone use

### 7. General Topic Handling

**Conversational groups**:
- General has its own Claude Code session (main branch worktree)
- Bot responds to messages directly
- Bot posts reminders here
- `$new` creates a new topic (carries context from current conversation)

**Dev group**:
- Same: General is a conversational topic
- `$new` creates a conversational topic

### 8. Message Routing

**Inbound (user → Claude)**:
- Check `topic_bindings` first (conversational topics)
- Fall back to `thread_bindings` (dev topics)
- For conversational: prefix with sender name
- Access control: non-DEV_USERS blocked from dev group

**Outbound (Claude → Telegram)**:
- Session matches `topic_binding` → send once to topic
- Session matches `thread_bindings` → send per-user (existing)

### 9. Migration

**france-2026 group** (becomes conversational):
- Close/delete all existing dev topics
- Keep General topic only
- Set up General with a conversational Claude session

**Dev group** (migrate existing "Dev Sam" group, formerly "Bot Dev"):
- Repurpose existing "Dev Sam" supergroup as the dev group
- Close/clean existing topics
- Create General conversational topic
- Create a dev session topic for every currently-open tmux session
- Bind all existing sessions

**State reset**: Clear state.json, session_map.json — fresh start (OK to lose history)

## Files to Modify

| File | Changes |
|---|---|
| `config.py` | CONVERSATIONAL_GROUPS, DEV_GROUP, DEV_USERS, DEV_GROUP_USER_TOPICS |
| `session.py` | topic_bindings, topic_types, multi-user routing, plan state |
| `bot.py` | Conversational topic flow, $plan, $accept, $new, multi-user, access control, General handling, GitHub links, tmux sync, subject change detection |
| `tmux_manager.py` | Remove --dangerously-skip-permissions, worktree creation |
| `handlers/directory_browser.py` | Skip for conversational topics |
| `handlers/message_queue.py` | Topic-level delivery |
| `session_monitor.py` | topic_bindings in session→topic resolution, tmux sync detection |
| `handlers/cleanup.py` | Clean up topic_bindings |
| `handlers/status_polling.py` | Skip status for conversational topics (already suppressed) |

### 10. Resolved Design Decisions

- **`$new` from General**: Carries relevant General conversation context to the new topic's session
- **Multiple `$accept`**: Yes — one conversational topic can spawn multiple work sessions over time
- **Work session completion**: Dev session notifies the source conversational topic with a summary
- **Session lifetime**: Auto-restart — kill idle conversational sessions after timeout, `--resume` on next message
- **Topic type rule**: Human-created topics = conversational (both group types). Dev topics = bot-created only (tmux sync or /accept)
- **Dev group user topics**: Configurable (`DEV_GROUP_USER_TOPICS`), defaults to "conversational", likely to change

## Implementation Phases

### Phase 1: Config + Migration ✅
- [x] New config: `CONVERSATIONAL_GROUPS`, `DEV_GROUP`, `DEV_USERS`, `DEV_GROUP_USER_TOPICS`
- [x] Create dev group (Telegram supergroup with forum topics)
- [x] Migrate france-2026: close all dev topics, keep General only
- [x] State reset (clear state.json, session_map.json)
- [x] Dev group General topic

### Phase 2: Conversational Topics ✅
- [x] `topic_bindings` + `topic_types` in session.py
- [x] Conversational topic creation flow (auto-session in main branch worktree)
- [x] Multi-user messaging (sender name prefix, single delivery)
- [x] General topic handling (bot responds directly)
- [x] Access control (non-DEV_USERS blocked from dev group)

### Phase 3: Dev Session Sync ✅
- [x] Bidirectional tmux ↔ dev topic sync
- [x] Detect new tmux sessions → auto-create dev topic
- [x] Dev topic close → kill tmux session (existing)
- [x] Tmux session kill → close dev topic
- [x] Startup reconciliation (create missing topics, clean stale ones)
- [x] Remove `--dangerously-skip-permissions` default for dev sessions

### Phase 4: Planning Flow ✅
- [x] `$plan` command (instructs Claude to enter planning mode)
- [x] `$accept` command (Sam only): extract plan → create worktree → write plan files → spawn dev session
- [x] Worktree creation (integrate with workspaces skill: pool file, naming, setup)
- [x] Dev session spawned in dev group with plan context
- [x] On completion: `$merge` command — merge worktree branch to main, release worktree
- [x] Claude instruction to suggest `$plan` when changes seem needed
- [x] `$new` command (create topic, carry context)

### Phase 5: Polish ✅
- [x] Subject change detection (AI-based, suggests $new with title) — via session instruction
- [x] GitHub links for referenced files — `_parse_github_url` + `_make_github_link` helpers
- [x] Auto-restart idle conversational sessions (dead session auto-restarts on next message)
- [x] Dev session completion → notification to source conversational topic (merge reminder)
- [x] Reminder routing to General topic (removed Reminders topic, sends to General)
- [x] Write `docs/retrospective.md` — reference doc with template
- [x] Auto-retrospective on $merge — triggers retro prompt to conversational session
- [x] Auto-retrospective on external completion — stale worktree monitor detects removed worktrees
- [ ] Retrospective of THIS implementation (our current plan) — one-off, do now

## Verification

- france-2026 group has only General topic after migration
- Sam and Nathalie can both send messages in conversational topics
- Claude responds conversationally, suggests $plan when appropriate
- Subject change detection suggests $new with a title
- $plan starts planning mode, $accept creates worktree + dev session
- Dev group has General + one dev topic per tmux session
- Creating a tmux session from terminal auto-creates dev topic
- Closing a dev topic kills the tmux session and vice versa
- General topic: bot responds directly, subject change → $new suggestion, reminders posted here
- Non-dev users blocked from dev group
- `uv run ruff check src/ && uv run pyright src/ccbot/`
