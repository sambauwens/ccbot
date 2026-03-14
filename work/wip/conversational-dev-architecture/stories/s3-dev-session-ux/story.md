# S3: Dev Session UX

## Context

Sam's explicit description of how dev topics are used:

> "for dev topics i'll default to using terminal through moshi, here the telegram dev session topic i plan to use only because it allows to post images files and that sort of stuff, and it's better at allowing me to input long messages [...] so the main purpose and UX focus should be it takes my input and then the claude session waits so i can switch back to terminal experience and continue work with the cli there, cause cli is better for most of the rest of dev."

> "The other thing where telegram dev session is better is the back and forth during planning mode, because the terminal can be clunky on phone for planning mode, for example when I'm writing the message I'm currently writing the phone only shows the first line. So it would be good to have a planning UX that maps this back and forth Q&A between claude and me."

## Design Constraints

1. **Telegram dev topic is a complement to terminal, not a replacement**
2. **Primary use cases**: send images/files, write long messages, planning mode Q&A on phone
3. **After sending input via Telegram**: Sam switches back to terminal (tmux attach)
4. **No `--dangerously-skip-permissions` by default** — permissions managed via terminal or $mode
5. **Status messages suppressed** — conversational only output (already done)

## Current State

- Dev topics auto-created from tmux sessions ✓
- Bidirectional sync (create/close either side) ✓
- Photos routed through topic_bindings ✓
- Voice messages supported ✓
- $mode for permission switching ✓
- Status messages suppressed ✓
- No --dangerously-skip-permissions default ✓

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | Review: does the current UX match "input then switch to terminal"? | done — yes, messages forward to tmux |
| T2 | Planning mode Q&A formatting for phone | not done — no special formatting for plan mode back-and-forth |

## Acceptance Criteria

- Dev topic works as input channel: text, images, voice all reach the tmux session
- Plan mode questions from Claude are formatted for easy phone reading
- No status/tool noise in dev topics (already suppressed)
