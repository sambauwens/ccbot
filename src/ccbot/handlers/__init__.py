"""Telegram bot handlers package — modular handler organization.

This package contains the Telegram bot handlers split by functionality:

Feature slices (vertical):
  - conversational: Conversational topic handling ($plan, $accept, $new, $merge)
  - dev_session_sync: Dev group session sync (auto-create/close topics)
  - session_commands: Session commands (/start, /history, /screenshot, etc.)
  - topic_lifecycle: Topic lifecycle events (created, closed, edited)
  - media: Photo, voice, unsupported content, forward command handlers
  - message_routing: Text message routing and outbound message delivery

Infrastructure (horizontal):
  - callback_data: Callback data constants (CB_* prefixes)
  - message_queue: Per-user message queue management
  - message_sender: Safe message sending helpers with MarkdownV2 fallback
  - history: Message history pagination
  - directory_browser: Directory selection UI
  - interactive_ui: Interactive UI (AskUserQuestion, Permission Prompt, etc.)
  - status_polling: Terminal status line polling
  - response_builder: Build paginated response messages
  - cleanup: Topic state cleanup on close/delete
"""
