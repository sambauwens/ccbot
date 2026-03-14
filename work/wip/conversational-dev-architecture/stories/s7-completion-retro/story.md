# S7: Completion, Merge, and Retrospective

## Context

From the planning discussion:

> on completion I should be reminded to do $merge to merge to main (Message 5, 14:49 UTC)

> if there are unmerged worktrees that are sitting there for a while, I should be reminded about them (Message 6, 14:51 UTC)

> when the plan is fully implemented we merge the worktree to main (Message 10, 14:25 UTC)

> when we are finished implementing a plan, we do a retrospective, using /session-explorer to introspect at the way we worked [...] basically a sort of agile retrospective I want to record for the future (Message 7, 15:09 UTC)

> whenever we are finished completing a plan that was started [...] it should also detect it for work that I started outside of a telegram conversation (Message 8, 15:15 UTC)

> and plan to write a retrospective instructions reference that we'll evolve over time [...] so the bot can prompt claude correctly when it triggers the retrospective (Message 9, 15:18 UTC)

## Current Implementation

### $merge command
- Merges worktree branch into default branch (main)
- Removes worktree, deletes branch
- Updates .workspace-pool.yml
- Pulls main worktree (ff-only)
- Triggers retrospective prompt to conversational session

### Merge reminder on session end
- `handle_session_removed` checks `_worktree_sources` for worktrees spawned via `$accept`
- Sends reminder to source conversational topic: "Run `$merge <name>` to merge to main"
- Limitation: `_worktree_sources` is in-memory only, lost on bot restart

### Stale worktree reminders
- `_check_stale_worktrees` in `reminder_monitor.py` runs on reminder interval
- Checks `.workspace-pool.yml` for reserved worktrees
- Uses latest commit date to determine staleness (not reservation date)
- Reminds after 2+ days idle
- Also detects externally removed worktrees and cleans pool file

### Auto-retrospective
- On `$merge`: sends retro prompt to conversational session referencing `docs/retrospective.md`
- On external worktree removal: detected by stale worktree monitor, notification sent
- `docs/retrospective.md` contains the guide with template

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | $merge command | done |
| T2 | Merge reminder when dev session ends | done (in-memory only) |
| T3 | Stale worktree reminders (idle 2+ days) | done |
| T4 | External worktree removal detection | done |
| T5 | Auto-retrospective on $merge | done |
| T6 | docs/retrospective.md reference doc | done |
| T7 | Persist _worktree_sources across restart | not done — source mapping lost on restart |

## Acceptance Criteria

- $merge merges to main, cleans up, triggers retro
- When a dev session ends, source conversational topic is reminded to $merge
- Worktrees idle 2+ days get daily reminders in the conversational group
- Externally removed worktrees are detected and pool file cleaned
- Retrospective prompt references docs/retrospective.md and instructs Claude to use /session-explorer
