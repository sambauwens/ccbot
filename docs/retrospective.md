# Retrospective Guide

How to run a retrospective after completing a plan's implementation. The bot triggers this automatically on `$merge` or when a worktree is detected as completed externally.

## When to Retrospect

- After `$merge` completes (bot-managed flow)
- When a reserved worktree is removed externally (detected by stale worktree monitor)
- Manually via `$retro` in a conversational topic

## Process

### 1. Timeline Reconstruction

Use `/session-explorer` to find all Claude Code sessions that worked in the worktree:
- What was the sequence of work?
- Where did the agent spend disproportionate time?
- What decision points occurred?
- What errors or course corrections happened?
- What else went wrong
- What did the user tell you to do differently than was intended? What problems and bugs were found (by user or agent)? during testing and other phases

Also check `git log` in the worktree branch for commit history.

### 2. Compare Actual vs Prescribed

Review how the work actually happened against what the claude instructions prescribe:
- Did the agent follow CLAUDE.md conventions?
- Were tests written first (TDD)?
- Were docs updated alongside code?
- Was the plan followed, or did scope creep in?
- Were there unnecessary tangents?

### 3. Five Whys for Problems

For each significant problem encountered, trace the root cause:
```
Problem: [what went wrong]
Why? → [immediate cause]
Why? → [deeper cause]
Why? → [systemic cause]
→ Root cause: [instruction gap / process gap / tooling gap]
```

### 4. Blameless Framing

Bad output = instruction gap, not agent failure. Focus on what can be changed:
- Instructions that were missing or ambiguous
- Process steps that were skipped
- Tooling that didn't support the workflow

### 5. What Went Well

Identify practices that produced good results:
- Approaches that saved time
- Patterns that should be repeated
- Tools or workflows that worked smoothly

## Output Format

### Policies (Instruction Changes)

Direct changes to CLAUDE.md, skills, or reference docs. Must be:
- Specific enough to implement immediately
- Traceable to evidence from this implementation

```
Evidence: [specific observation]
→ Analysis: [why this happened]
→ Policy: [instruction change]
```

### Projects (Structural Work)

Changes requiring design or implementation beyond instruction edits:
- New tooling needed
- Process redesign
- Research questions

```
Evidence: [specific observation]
→ Analysis: [why this matters]
→ Project: [what to build/change]
```

## Retrospective Document Template

```markdown
# Retrospective: [plan name]

**Project**: [project name]
**Worktree**: [worktree name]
**Duration**: [start date] → [end date]
**Sessions**: [number of Claude Code sessions involved]

## Timeline Summary
| When | What | Notes |
|------|------|-------|
| ... | ... | ... |

## What Went Well
- ...

## What Went Wrong
- ...

## Root Cause Analysis
### [Problem 1]
- Why? → ...
- Why? → ...
- Root cause: ...

## Outputs

### Policies
- [Evidence] → [Policy change]

### Projects
- [Evidence] → [Project to create]

## Process Assessment
- Did we follow TDD? [yes/no, details]
- Did we update docs alongside code? [yes/no, details]
- Did we follow the plan? [yes/no, scope changes]
- Learning velocity: [fast/normal/slow, why]
```

## Sources

Adapted from:
- Google SRE blameless postmortem practices
- Five Whys technique (Toyota Production System)
- golfmini ai-meta-research retrospective methodology
