# S1: Conversational Session Permission Lifecycle

## Context

Sam's requirement: "allow all reads... when something needs to be changed: i want all changes to go through a planning phase first." And critically: "during plan mode I think it's fine to launch a --danger... session until the plan is accepted, at which point we delegate to a new session anyway."

The permission model has three states:

```
READ-ONLY (default) → $plan → ELEVATED (full permissions) → $accept → delegates to worktree, returns to READ-ONLY
```

## Problem Analysis

The initial implementation got this wrong three times:
1. First: `--dangerously-skip-permissions` always (too permissive — Claude wrote files without being asked)
2. Then: `--allowedTools` read-only always (too restrictive — can't write plans during $plan)
3. Then: started adding `--resume` to the read-only restart (wrong — the permission elevation is the fix)

The root cause: treating permissions as static per-session instead of as a lifecycle tied to the planning workflow.

## Permission States

### State 1: Read-Only (default)
- `--allowedTools Read,Glob,Grep,Agent,WebSearch,WebFetch,LSP`
- Claude can read anything, do web research, launch research sub-agents
- Claude CANNOT write, edit, or run bash
- Claude is instructed to suggest `$plan` when conversation leads toward changes
- This is the state when a conversational topic is first created

### State 2: Elevated ($plan active)
- `--dangerously-skip-permissions`
- Claude CAN write plan files, explore, research, write structured plans
- Triggered by `$plan` command
- Session is killed and restarted with --resume + elevated permissions
- Stays elevated until `$accept` or explicit de-escalation

### State 3: Delegated ($accept)
- $accept creates a worktree + dev session (separate topic in dev group)
- The conversational session returns to read-only
- Session is killed and restarted with --resume + read-only permissions
- The worktree session runs with appropriate dev permissions

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | Implement $plan permission elevation (kill read-only, restart with --dangerously-skip-permissions + --resume) | needs rework |
| T2 | Implement $accept de-escalation (after creating worktree, kill elevated, restart read-only + --resume) | not done |
| T3 | Track permission state per topic (read-only vs elevated) in session state | not done |
| T4 | Ensure the instruction prompt ("suggest $plan") is only sent in read-only state, not after elevation | not done |

## Acceptance Criteria

- New conversational topic: Claude can read files and do web research but CANNOT write
- User runs `$plan`: Claude can now write plan files, explore, do everything
- User runs `$accept`: worktree created, dev session spawned, conversational session returns to read-only
- At no point does Claude write files outside the planning workflow
- Permission state survives session auto-restart (--resume)
