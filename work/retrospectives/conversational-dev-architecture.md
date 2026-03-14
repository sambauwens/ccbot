# Retrospective: Conversational + Dev Topic Architecture

**Project**: ccbot
**Duration**: 2026-03-14 (00:15 – 16:55 UTC)
**Sessions**: 4 Claude Code sessions (main: 82eb2978)
**Commits**: 10

## Timeline Summary

| When (UTC) | What | Phase |
|------|------|-------|
| 00:15 | Sam reports noisy "Crunched for 38s" status messages | Pre |
| 00:25 | Multiple corrections needed to understand "suppress all status" | Pre |
| 06:24 | Sam articulates full architecture vision (2 topic types, 2 group types) | Design |
| 06:24–12:06 | Extended design discussion, 16 AskUserQuestion prompts | Design |
| 12:06 | Major plan correction: "dev group should have both topic types" | Design |
| 12:51 | Phase 1: Config + Migration | P1 |
| 13:05 | Phase 2: Conversational Topics | P2 |
| 13:24 | Sam reminds: "follow TDD, update docs alongside code" | P2 |
| 13:34 | Commit phases 1-2 (tests + docs added retroactively) | P2 |
| 13:35 | Phase 3: Dev Session Sync | P3 |
| 13:42 | Sam catches: "I don't see dev topics" — reconciliation missing | P3 |
| 13:45 | Sam catches: "startup reconciliation was supposed to be in Phase 3" | P3 |
| 14:20 | Phase 3 complete (after fix) | P3 |
| 14:25 | france-2026 converted to worktrees | P3 |
| 14:42 | Phase 4: Planning Flow ($plan, $accept, $new, $merge) | P4 |
| 15:01 | Phase 4 complete | P4 |
| 15:24 | Phase 5: Polish (GitHub links, retro doc, auto-restart) | P5 |
| 15:33 | Sam: "bot.py is huge, split it" — architecture refactor | P5 |
| 15:54 | Refactor complete: bot.py 2900→1145 lines, 6 new handler modules | P5 |

## What Went Well

1. **Extended design before code**: ~6 hours of iterative design with 16 clarification questions before writing code. Produced a solid, well-understood architecture.
2. **Rapid implementation**: 5 phases in ~3 hours once design was settled (12:51–15:54), 10 commits.
3. **Bot restarted consistently**: 28 restarts — agent always followed the "restart after changes" rule.
4. **Refactor validated test quality**: bot.py split required only 1 test file change (import paths), confirming tests were behavior-focused.
5. **Plan as living document**: Updated ~25 times, checkboxes added for tracking, kept in sync with backup.
6. **Agent saved process feedback as memory**: "review before done", "TDD", "agent permissions" — learnings persisted for future sessions.

## What Went Wrong

### 1. Premature "done" on Phase 3
Agent declared Phase 3 complete without implementing startup reconciliation — an item explicitly listed in the plan. Sam caught it.

**Five Whys**:
- Why was it missed? → Agent didn't re-read the plan checklist before declaring done
- Why not? → No habit/process for self-review
- Why? → Agent optimized for speed over thoroughness
- Root cause: **No systematic completion checklist verification**

### 2. TDD not followed until reminded
Phases 1-2 were implemented without tests. Tests were added retroactively after Sam reminded at 13:24.

**Five Whys**:
- Why? → Agent was focused on getting the feature working
- Why? → Tests felt secondary to the implementation
- Root cause: **TDD not internalized — treated as optional rather than required process**

### 3. Docs not updated until reminded
Same as TDD — docs were added retroactively, then Sam had to correct the location (.claude/references → docs/).

### 4. Multiple corrections for status message suppression
Took 3 rounds (00:15, 00:25, 00:31) to understand Sam wanted ALL status messages suppressed, not just summary ones.

**Root cause**: Agent implemented the narrowest interpretation first instead of asking for clarification.

### 5. Plan missed topic type in dev group
Agent modeled dev group as dev-only. Sam had to correct: "for dev mode I said I want conversational topics as well."

**Root cause**: Agent built a mental model and didn't validate it against Sam's actual words.

### 6. Sub-agent permission error
First refactor agent launched without Write/Bash access. Wasted ~4 minutes.

### 7. No pyright or ruff format runs
CLAUDE.md requires both before committing. Neither was run during this implementation.

## User Corrections (14 total)

| # | Category | Correction |
|---|----------|------------|
| 1 | Scope | "Don't mirror all those types of messages" |
| 2 | Output | "I only want to see 'Done' and questions" |
| 3 | Intent | "I meant change the code for that" |
| 4 | Design | "Remove --dangerously-skip-permissions" |
| 5 | Design | "Dev group should have both topic types" |
| 6 | Process | "Follow TDD, update docs alongside code" |
| 7 | Location | "Move docs to docs/" |
| 8 | Thoroughness | "Did you update all the links?" |
| 9 | Completeness | "I don't see dev topics" (Phase 3 incomplete) |
| 10 | Process | "Startup reconciliation was supposed to be in Phase 3" |
| 11 | Process | "Always review before saying you're finished" |
| 12 | UX | "Make all points checkboxes" |
| 13 | Feature | "I should be reminded to $merge" |
| 14 | Architecture | "bot.py is huge, split it" |

## Outputs

### Policies (Instruction Changes)

| Evidence | Policy |
|----------|--------|
| Phase 3 declared done prematurely | Agent must re-read plan checklist before declaring any phase complete (saved as memory) |
| TDD not followed until reminded | Tests must be written BEFORE implementation, verify red→green (saved as memory) |
| Docs missed, wrong location | Docs updated alongside code, live in docs/ not .claude/references (saved as memory) |
| Sub-agent launched without Write | Use general-purpose agents for tasks needing Write/Bash (saved as memory) |
| Agent gave narration mid-task | Only output questions or done summaries (saved as memory) |
| Bot commands could clash with Claude | Use $ prefix for bot commands (saved as memory) |

### Projects (Structural Work)

| Evidence | Project |
|----------|---------|
| No pyright/format runs | Add pre-commit hook or CI check for pyright + ruff format |
| GitHub link helpers exist but injection is fragile (regex-based) | Consider using Claude's structured output to identify file references rather than regex post-processing |
| 14 user corrections in one session | Review whether the design discussion phase should be more structured (e.g., requirement checklist) |

## Process Assessment

- **TDD**: Not followed initially, adopted after reminder. Tests were nearly simultaneous with code — scaffolded rather than truly driving design.
- **Docs**: Updated after reminder. Location corrected. Maintained well after that.
- **Plan followed**: Yes, with corrections. Plan was a living document, updated throughout.
- **Learning velocity**: Fast once the design settled. The 6-hour design phase was appropriate given the architectural scope.
- **Architecture**: Final refactor (screaming architecture) was clean — validates the approach. Should have been considered earlier rather than building a 2900-line monolith first.
