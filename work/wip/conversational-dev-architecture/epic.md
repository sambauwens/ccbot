# Epic: Conversational + Dev Topic Architecture

## Goal

Enable non-developers (Nathalie) to collaborate on projects through Telegram, where Claude helps research and plan, and Sam approves before code changes happen. Simultaneously, make dev sessions safer through workflow-based safety (plan first, implement after acceptance) rather than permission flags.

## User's Requirements (verbatim from planning session)

### The core vision (Message 3, 07:23 UTC)

> i think there should be 2 types of telegram topics: work/dev session topics so i can discuss with my session, to allow me to send photos and "talk to the session through chat" and where its a different process cause its a developer mode type session, these mostly map what we already have before these new requirements. and conversational topics that are not tied to sessions, where we can include non developers like nathalie so we can do the things i described and ask questions, where you can also send images to, where you also give us links to the content (mostly the md files) in github so we can read them there (we nathalie and me both have access to github), where we do the planning i specified, and then it launches a new separate work/dev session (that will also be tied to a topic like)

### Permission model (Messages 1-2, 06:24-06:35 UTC)

> remove the dangerously skip by default. what i want is to allow all reads, so the agent can read anything from anywhere on the machine and same for web [...] for web it should launch a research agent and all websites are automatically approved (since research is about reading, that is fine for me).
>
> when something needs to be changed (write side): i want all changes to go through a planning phase first, then we use auto-edit once the plan is accepted. with only write rights inside the project allowed, and not allowed to change claude instructions without my approval.

### Permission elevation during planning (AskUserQuestion #7)

> claude should be able to write the plan, when I gave the requirements I didn't mean it has to launch claude in readonly, I gave specific requirements on what should be read and what should be written (maybe I was not clear so no worries), so actually during plan mode I think it's fine to launch a `--danger...` session until the plan is accepted, at which point we delegate to a new session anyway, do you catch the distinction?

### Conversational topics are higher-order (AskUserQuestion #2, #6)

> shared session, and I'm still unsure whether these topics should even directly be tied to a claude session, or if it's higher order, where the bot would be a broker between the conversation and the claude sessions necessary to support it

> we should not switch cause we want to stay open to different claude integration models. you know cause conversational would likely need to be higher order, beyond a single session, remember it spawns sessions

### Dev session UX purpose (AskUserQuestion #6)

> for dev topics i'll default to using terminal through moshi, here the telegram dev session topic i plan to use only because it allows to post images files and that sort of stuff, and it's better at allowing me to input long messages [...] so the main purpose and UX focus should be it takes my input and then the claude session waits so i can switch back to terminal experience and continue work with the cli there, cause cli is better for most of the rest of dev. The other thing where telegram dev session is better is the back and forth during planning mode, because the terminal can be clunky on phone

### Conversational topics are not always about planning (AskUserQuestion #8)

> I meant that conversations in conversational topics do not necessarily all lead to planning new work, a lot of it can just be questions and answers, the bot should detect the difference (using claude AI obviously) and ask whether we want to change something [...] and users can always use $plan to start planning work.

### Work output as worktree with plan (AskUserQuestion #2)

> create a new work/wip dir with the plan as epic-story-task type of plan, including all the details from planning exploration and research agents, so it has all the context needed to implement the plan

### Group model (AskUserQuestion #4, Message 4)

> I want to separate "conversational" groups from dev groups, that way the membership model is clear.

> for dev mode I said I want conversational topics as well [...] we basically have these 2 types of topics, and we could technically use them in any group, for now I decide there are "Conversational groups" that only have conversational topics and a "Dev Group" that can have both, and has all the dev session topics.

### Subject change detection (ExitPlanMode rejection #2)

> "Every bot response ends with: /new to discuss this in a dedicated topic", not for every message, only when it detects we're talking about a new topic in a conversation where we're talking about something else. So it does this in any conversational topic, this subject change detection, and in the suggestion it already suggests the title of the new topic.

### Tmux sync (Message 4)

> the dev group starts with a "General" conversational topic, and then there is a dev session for every open tmux "window", and it keeps it automatically mirrored (if I create a window from somewhere else or when a dev topic is created from the bot/telegram side).

### On completion (Message 5-6, 10)

> on completion I should be reminded to do $merge to merge to main
> if there are unmerged worktrees that are sitting there for a while, I should be reminded about them
> when the plan is fully implemented we merge the worktree to main

### Retrospective (Messages 7-9)

> when we are finished implementing a plan, we do a retrospective, using /session-explorer to introspect at the way we worked [...] basically a sort of agile retrospective I want to record for the future
> whenever we are finished completing a plan that was started [...] it should also detect it for work that I started outside of a telegram conversation

## Key Design Decisions (from discussion)

1. **Human-created topics = conversational** (both group types). Dev topics = bot-created only (tmux sync or $accept).
2. **$new context carry**: yes, carry relevant context to new topic's session.
3. **Multiple $accept**: one conversational topic can spawn multiple work sessions.
4. **Dev session completion**: notifies source conversational topic with summary.
5. **Session lifetime**: auto-restart idle sessions with --resume on next message.
6. **$ prefix**: all bot commands use $ (not /) to avoid Claude Code command confusion.
7. **GitHub links**: always link directly to the right header anchor where one matches.

## Stories

| # | Story | Status | Details |
|---|-------|--------|---------|
| S1 | Conversational session permission lifecycle | ✅ done | [→ story](stories/s1-permission-lifecycle/story.md) — Read-only by default, $plan elevates via kill+restart+resume, $accept de-escalates back. Permission state persisted. |
| S2 | Conversational topic as broker | ✅ done | [→ story](stories/s2-conversational-broker/story.md) — Documented as broker model. Code uses topic_bindings (per-topic, not per-session). |
| S3 | Dev session UX | ✅ done | [→ story](stories/s3-dev-session-ux/story.md) — Input channel for terminal work. Photos, voice, text all forward to tmux. |
| S4 | $plan → $accept → worktree flow | ✅ done | [→ story](stories/s4-plan-accept-flow/story.md) — Plan written while elevated, then de-escalation to read-only. Worktree creation, pool update, dev session spawning all work. |
| S5 | Subject change detection | ✅ done (needs manual testing) | [→ story](stories/s5-subject-detection/story.md) — Via session instruction. |
| S6 | Tmux bidirectional sync | ✅ done | [→ story](stories/s6-tmux-sync/story.md) — Full bidirectional sync with startup reconciliation. |
| S7 | Completion + retrospective | ✅ done | [→ story](stories/s7-completion-retro/story.md) — $merge, stale reminders, auto-retro. worktree_sources now persisted. |
| S8 | GitHub links | ✅ done (header anchors deferred) | [→ story](stories/s8-github-links/story.md) — Link injection + remote URL caching. Header anchor matching deferred. |
