# S4: $plan → $accept → Worktree Flow

## Context

The full planning lifecycle from Sam's requirements:

1. Conversational session is read-only (S1)
2. User runs `$plan` → session elevated to full permissions (S1)
3. Claude explores, researches, writes plan files — back-and-forth Q&A with users
4. User runs `$accept <name>` → creates worktree, writes plan, spawns dev session
5. Conversational session returns to read-only (S1)
6. Dev session implements in the worktree
7. On completion: `$merge` → merge to main, release worktree, trigger retrospective

### Plan output format (AskUserQuestion #2)

> "create a new work/wip dir with the plan as epic-story-task type of plan, including all the details from planning exploration and research agents, so it has all the context needed to implement the plan"

### Worktree model (AskUserQuestion #4)

> "I want each work to be done in a worktree, so the bot needs to make sure the project has a worktree when onboarding on a new project (e.g when we create a group for it), and if it doesn't then it uses the skill to transform the project to a worktree project"

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | $plan command: elevate permissions (S1 T1) | needs rework |
| T2 | $accept: create worktree from bare repo | done |
| T3 | $accept: update .workspace-pool.yml | done |
| T4 | $accept: write plan files in epic-story-task format to work/wip/ | needs rework — currently asks conversational Claude to write, but after $accept the session returns to read-only |
| T5 | $accept: spawn dev session + topic in dev group | done |
| T6 | $accept: de-escalate conversational session back to read-only (S1 T2) | not done |
| T7 | $accept: notify conversational topic with link to dev topic | done |
| T8 | $merge: merge worktree to main, remove worktree, update pool | done |
| T9 | $merge: trigger retrospective | done |
| T10 | Stale worktree reminders (idle 2+ days based on last commit) | done |
| T11 | Project onboarding: detect non-worktree projects, offer conversion | not done |

## Problem: T4 — Plan File Writing

The $accept command currently sends a prompt to the conversational Claude to write the plan. But after $accept, the conversational session should return to read-only (S1). Options:

**Option A**: Write the plan BEFORE $accept de-escalates (while still elevated from $plan)
- Pro: Claude has the context and permissions
- Con: Timing is tricky — need to wait for Claude to finish writing before de-escalating

**Option B**: The bot extracts plan content from the conversation and writes the files itself
- Pro: No dependency on Claude for file writing
- Con: Bot can't produce the same quality plan as Claude

**Option C**: The NEW dev session writes the plan as its first task
- Pro: Clean separation — the dev session has the plan context from the prompt
- Con: The dev session starts without the full conversational context

Recommended: **Option A** — $accept tells Claude to write the plan NOW (while elevated), waits for completion, then creates worktree, moves plan files, spawns dev session, de-escalates.

## Acceptance Criteria

- $plan elevates the conversational session to full permissions
- Back-and-forth Q&A works during planning (Claude can research, write drafts)
- $accept produces a work/wip/<name>/ directory with epic.md + stories/
- Worktree created from bare repo with correct naming and pool file update
- Dev session spawned in dev group with plan as context
- Conversational session returns to read-only after $accept
- $merge merges, cleans up, triggers retrospective
- Stale worktrees get daily reminders
