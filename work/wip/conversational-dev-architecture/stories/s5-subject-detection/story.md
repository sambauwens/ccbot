# S5: Subject Change Detection

## Context

From the planning discussion (ExitPlanMode rejection #2):

> "Every bot response ends with: /new to discuss this in a dedicated topic", not for every message, only when it detects we're talking about a new topic in a conversation where we're talking about something else. So it does this in any conversational topic, this subject change detection, and in the suggestion it already suggests the title of the new topic.

And from AskUserQuestion #8:

> I meant that conversations in conversational topics do not necessarily all lead to planning new work, a lot of it can just be questions and answers, the bot should detect the difference (using claude AI obviously) and ask whether we want to change something [...] and users can always use $plan to start planning work.

## Problem Analysis

Two related detections needed:
1. **Subject drift** — conversation shifts to a new topic → suggest `$new <title>`
2. **Change intent** — conversation leads toward code changes → suggest `$plan`

Both should be AI-based (Claude detects it naturally), not on every message, and with pre-suggested titles/names.

## Current Implementation

Both detections are handled via the session instruction prompt sent when a conversational session is created. Claude is told to suggest `$plan` and `$new <title>` when appropriate. This is the simplest approach — no separate classification step.

Potential issue: the instruction competes with conversation context. If Claude gets deep into a topic, it may forget to suggest `$new` or `$plan`. This hasn't been tested at scale.

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | Session instruction includes both $plan and $new suggestions | done |
| T2 | Verify Claude actually suggests $new when subject drifts (manual test) | not done |
| T3 | Verify Claude actually suggests $plan when changes seem needed (manual test) | not done |
| T4 | Consider: should the bot post-process responses to detect if Claude forgot to suggest? | not started — deferred until we see if T2/T3 work |

## Acceptance Criteria

- Claude suggests `$new <suggested-title>` when conversation genuinely shifts subject
- Claude suggests `$plan` when conversation leads toward code changes that need implementation
- Neither suggestion appears on every message — only on genuine shifts
- The suggested title in `$new` is meaningful and relevant
