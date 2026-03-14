# S2: Conversational Topic as Broker

## Context

Sam repeatedly emphasized that conversational topics are "higher order" — not a 1:1 pipe to a Claude session. From the planning discussion:

> "I'm still unsure whether these topics should even directly be tied to a claude session, or if it's higher order, where the bot would be a broker between the conversation and the claude sessions necessary to support it"

> "we should not switch cause we want to stay open to different claude integration models. you know cause conversational would likely need to be higher order, beyond a single session, remember it spawns sessions"

## Problem Analysis

The current implementation treats a conversational topic as exactly 1 Claude Code session — same as a dev topic, just with different permissions. This misses the "broker" nature:

- A conversational topic can spawn dev sessions ($accept)
- The same topic can spawn multiple work sessions over time
- The topic carries context across session restarts
- The topic mediates between multiple users and the Claude session
- The bot adds value on top: sender name prefixing, GitHub links, subject change detection, $command handling

The current 1:1 model works for now (Sam said "I suppose A if it makes it simpler and fulfills all requirements, but don't formalize on this, it could be multiple claude code read sessions if necessary"), but the architecture should not assume 1:1.

## Current State

The current implementation does handle the broker aspects partially:
- `topic_bindings` (per-topic, not per-user) ✓
- Multi-user message routing with sender prefix ✓
- $commands intercepted by the bot before reaching Claude ✓
- GitHub link post-processing on responses ✓
- Permission lifecycle managed by the bot (S1) ✓
- Multiple $accept from same topic ✓

What's missing:
- No explicit "broker" abstraction — the topic-to-session mapping is just a dict
- No way to have multiple Claude sessions per topic (e.g., parallel research agents)
- Session context is lost if the JSONL file is too large / compacted

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | Document the broker model as an architectural decision (not just implementation detail) | not done |
| T2 | Ensure the code doesn't hard-assume 1:1 topic-to-session (no breaking changes needed, just review) | not done |

## Acceptance Criteria

- The architecture docs describe conversational topics as a broker, not a session wrapper
- Code review confirms no hard 1:1 assumptions that would block future multi-session support
- The 1:1 model continues working as the current implementation
