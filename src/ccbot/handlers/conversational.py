"""Conversational topic handlers — $plan, $accept, $new, $merge commands.

Manages conversational sessions in managed Telegram groups (conversational + dev).
Topics bound via topic_bindings get multi-user, name-prefixed messaging with
support for planning workflows and worktree management.

Key functions:
  - handle_conversational_message: Route messages in conversational topics
  - _handle_plan_command: Enter planning mode
  - _handle_accept_command: Accept plan, create worktree, spawn dev session
  - _handle_new_command: Create new conversational topic with context carry
  - _handle_merge_command: Merge worktree branch to main
  - _parse_github_url / _make_github_link / _inject_github_links: GitHub link helpers
"""

import logging
import re
import subprocess
import time
from pathlib import Path

import yaml

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ..config import config
from ..session import session_manager
from ..tmux_manager import tmux_manager
from .message_sender import safe_reply

logger = logging.getLogger(__name__)


def _resolve_conversational_dir(project_name: str) -> str:
    """Find the directory for a conversational session.

    Uses the main branch worktree if the project has one, otherwise the project dir.
    """
    project_dir = config.project_dir(project_name)
    main_worktree = project_dir / f"{project_name}-main"
    if main_worktree.is_dir():
        return str(main_worktree)
    return str(project_dir)


def _parse_github_url(remote_url: str) -> str | None:
    """Parse a GitHub HTTPS URL from a git remote URL. Returns None for non-GitHub."""
    import re as _re

    # HTTPS: https://github.com/owner/repo.git
    m = _re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?$", remote_url)
    if m:
        return f"https://github.com/{m.group(1)}"
    # SSH: git@github.com:owner/repo.git
    m = _re.match(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", remote_url)
    if m:
        return f"https://github.com/{m.group(1)}"
    return None


def _make_github_link(
    base_url: str, file_path: str, branch: str, heading: str | None = None
) -> str:
    """Build a GitHub blob link, optionally with a header anchor."""
    url = f"{base_url}/blob/{branch}/{file_path}"
    if heading:
        # GitHub anchor format: lowercase, spaces->hyphens, strip non-alphanumeric
        anchor = re.sub(r"[^\w\s-]", "", heading.lower())
        anchor = re.sub(r"\s+", "-", anchor.strip())
        url = f"{url}#{anchor}"
    return url


def _inject_github_links(text: str, work_dir: str) -> str:
    """Post-process text to append GitHub links for referenced file paths.

    Scans for file paths relative to the work_dir and appends clickable
    GitHub links. Only processes .md files and common code files.
    """
    try:
        remote_url = subprocess.run(
            ["git", "-C", work_dir, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return text

    base_url = _parse_github_url(remote_url)
    if not base_url:
        return text

    work_path = Path(work_dir).resolve()

    # Find file paths in the text (patterns like path/to/file.ext or /abs/path/to/file.ext)
    path_pattern = re.compile(
        r"(?:^|[\s`(])(/[\w./-]+\.(?:md|py|ts|js|yml|yaml|json|toml))\b"
        r"|(?:^|[\s`(])([\w][\w./-]+\.(?:md|py|ts|js|yml|yaml|json|toml))\b",
        re.MULTILINE,
    )

    replacements: list[tuple[str, str]] = []
    for m in path_pattern.finditer(text):
        file_ref = m.group(1) or m.group(2)
        if not file_ref:
            continue

        # Resolve to absolute, then make relative to work_dir
        if file_ref.startswith("/"):
            abs_path = Path(file_ref)
        else:
            abs_path = work_path / file_ref

        try:
            abs_path = abs_path.resolve()
            if not abs_path.is_file():
                continue
            rel_path = abs_path.relative_to(work_path)
        except (ValueError, OSError):
            continue

        link = _make_github_link(base_url, str(rel_path), "main")
        link_text = f"[{rel_path}]({link})"
        # Only replace if not already a markdown link
        if f"]({link}" not in text:
            replacements.append((file_ref, link_text))

    # Apply replacements (longest first to avoid partial matches)
    for old, new in sorted(replacements, key=lambda x: -len(x[0])):
        text = text.replace(old, new, 1)

    return text


async def _handle_plan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    window_id: str,
    arg: str,
) -> None:
    """Handle $plan -- instruct Claude to enter planning mode."""
    assert update.message is not None
    prompt = (
        "Enter plan mode. The user wants to plan changes to the codebase. "
        "Explore the codebase, research what's needed, and produce a structured plan. "
        "Use back-and-forth questions to clarify requirements before finalizing."
    )
    if arg:
        prompt = f"{prompt}\n\nContext: {arg}"

    first_name = getattr(update.effective_user, "first_name", "User")
    prefixed = f"[{first_name}] {prompt}"
    success, msg = await session_manager.send_to_window(window_id, prefixed)
    if not success:
        await safe_reply(update.message, f"❌ {msg}")


async def _handle_accept_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    window_id: str,
    arg: str,
) -> None:
    """Handle $accept -- extract plan, create worktree, spawn dev session.

    Only DEV_USERS can accept plans.
    """
    from ..bot import _topic_names, _worktree_sources, _sanitize_tmux_name

    assert update.message is not None

    if not config.is_dev_user(user_id):
        await safe_reply(update.message, "❌ Only dev users can accept plans.")
        return

    project_name = config.project_for_group(chat_id)
    if not project_name:
        await safe_reply(update.message, "❌ No project mapped to this group.")
        return

    # Determine plan name from arg or ask Claude to summarize
    plan_name = arg.strip() if arg.strip() else None
    if not plan_name:
        await safe_reply(
            update.message,
            "Usage: `$accept <plan-name>`\nExample: `$accept add-login-page`",
        )
        return

    plan_slug = _sanitize_tmux_name(plan_name)
    worktree_name = f"{project_name}-{plan_slug}-ws"

    # Create worktree from bare repo
    project_dir = config.project_dir(project_name)
    bare_repo = project_dir / f"{project_name}.git"
    worktree_path = project_dir / worktree_name

    if not bare_repo.is_dir():
        await safe_reply(
            update.message,
            f"❌ Project not set up for worktrees (missing {bare_repo}).",
        )
        return

    if worktree_path.exists():
        await safe_reply(
            update.message,
            f"❌ Worktree `{worktree_name}` already exists.",
        )
        return

    await safe_reply(update.message, f"Creating worktree `{worktree_name}`...")

    try:
        # Create branch and worktree
        subprocess.run(
            [
                "git",
                "-C",
                str(bare_repo),
                "worktree",
                "add",
                str(worktree_path),
                "-b",
                worktree_name,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        await safe_reply(update.message, f"❌ Failed to create worktree: {e.stderr}")
        return

    # Update pool file
    pool_file = project_dir / ".workspace-pool.yml"
    if pool_file.exists():
        try:
            pool_data = yaml.safe_load(pool_file.read_text()) or {}
            if "worktrees" not in pool_data:
                pool_data["worktrees"] = {}
            pool_data["worktrees"][worktree_name] = {
                "status": "reserved",
                "branch": worktree_name,
                "agent": "claude",
                "epic": plan_name,
                "reserved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            pool_file.write_text(yaml.dump(pool_data, default_flow_style=False))
        except Exception as e:
            logger.warning("Failed to update pool file: %s", e)

    # Ask conversational Claude to write the plan to the worktree
    plan_prompt = (
        f"Write a structured implementation plan to {worktree_path}/.claude/plans/{plan_slug}.md — "
        f"include epic, stories, tasks, and all research context from our conversation. "
        f"This plan will be used by a new Claude session to implement the changes."
    )
    await session_manager.send_to_window(window_id, plan_prompt)

    # Spawn dev session in the worktree
    success, message, created_name, created_wid = await tmux_manager.create_window(
        str(worktree_path), window_name=worktree_name
    )
    if not success:
        await safe_reply(update.message, f"❌ Failed to create dev session: {message}")
        return

    await session_manager.wait_for_session_map_entry(created_wid, timeout=5.0)

    # Create dev topic in dev group
    dev_chat_id = config.dev_group
    if dev_chat_id:
        try:
            forum_topic = await context.bot.create_forum_topic(
                chat_id=dev_chat_id,
                name=worktree_name,
            )
            new_thread_id = forum_topic.message_thread_id
            for uid in config.dev_users:
                session_manager.set_group_chat_id(uid, new_thread_id, dev_chat_id)
                session_manager.bind_thread(
                    uid,
                    new_thread_id,
                    created_wid,
                    window_name=created_name,
                )
            _topic_names[(dev_chat_id, new_thread_id)] = worktree_name
            _worktree_sources[worktree_name] = (chat_id, thread_id)

            # Notify conversational topic with link
            link_chat_id = str(dev_chat_id).replace("-100", "")
            topic_link = f"https://t.me/c/{link_chat_id}/{new_thread_id}"
            await safe_reply(
                update.message,
                f"Plan accepted. Work session created: [{worktree_name}]({topic_link})",
            )
        except Exception as e:
            logger.error("Failed to create dev topic for accepted plan: %s", e)
            await safe_reply(
                update.message,
                f"Worktree created at `{worktree_path}`, but failed to create dev topic: {e}",
            )
    else:
        await safe_reply(
            update.message,
            f"Worktree created at `{worktree_path}`. No dev group configured.",
        )


async def _handle_new_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    window_id: str,
    arg: str,
) -> None:
    """Handle $new -- create a new conversational topic, carrying context."""
    from ..bot import _topic_names, _sanitize_tmux_name

    assert update.message is not None

    topic_title = arg.strip() if arg.strip() else "New conversation"

    try:
        forum_topic = await context.bot.create_forum_topic(
            chat_id=chat_id,
            name=topic_title,
        )
        new_thread_id = forum_topic.message_thread_id
    except Exception as e:
        await safe_reply(update.message, f"❌ Failed to create topic: {e}")
        return

    _topic_names[(chat_id, new_thread_id)] = topic_title

    # Create a new conversational session for the new topic
    project_name = config.project_for_group(chat_id)
    if project_name:
        work_dir = _resolve_conversational_dir(project_name)
        tmux_name = f"{project_name}-{_sanitize_tmux_name(topic_title)}"

        success, message, created_name, created_wid = await tmux_manager.create_window(
            work_dir,
            window_name=tmux_name,
            allowed_tools="Read,Glob,Grep,Agent,WebSearch,WebFetch,LSP"
        )
        if success:
            await session_manager.wait_for_session_map_entry(created_wid, timeout=5.0)
            session_manager.bind_topic(
                chat_id,
                new_thread_id,
                created_wid,
                topic_type="conversational",
                window_name=created_name,
            )

            # Carry context: ask the old session to summarize for the new one
            summary_prompt = (
                f"Summarize the current conversation context concisely — "
                f"the user is moving to a new topic '{topic_title}'. "
                f"Output only the summary, nothing else."
            )
            await session_manager.send_to_window(window_id, summary_prompt)

    # Reply with link to new topic
    link_chat_id = str(chat_id).replace("-100", "")
    topic_link = f"https://t.me/c/{link_chat_id}/{new_thread_id}"
    await safe_reply(
        update.message,
        f"Created [{topic_title}]({topic_link})",
    )


async def _handle_merge_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    thread_id: int | None,
    window_id: str,
    arg: str,
) -> None:
    """Handle $merge -- merge a worktree branch to main and release it.

    Only DEV_USERS can merge.
    """
    assert update.message is not None

    if not config.is_dev_user(user_id):
        await safe_reply(update.message, "❌ Only dev users can merge.")
        return

    project_name = config.project_for_group(chat_id)
    if not project_name:
        await safe_reply(update.message, "❌ No project mapped to this group.")
        return

    worktree_name = arg.strip() if arg.strip() else None
    if not worktree_name:
        await safe_reply(
            update.message,
            "Usage: `$merge <worktree-name>`\nExample: `$merge france-2026-add-login-ws`",
        )
        return

    project_dir = config.project_dir(project_name)
    bare_repo = project_dir / f"{project_name}.git"
    worktree_path = project_dir / worktree_name

    if not worktree_path.is_dir():
        await safe_reply(update.message, f"❌ Worktree `{worktree_name}` not found.")
        return

    # Get default branch from pool file
    pool_file = project_dir / ".workspace-pool.yml"
    default_branch = "main"
    if pool_file.exists():
        try:
            pool_data = yaml.safe_load(pool_file.read_text()) or {}
            default_branch = pool_data.get("default_branch", "main")
        except Exception:
            pass

    try:
        # Merge worktree branch into default branch
        subprocess.run(
            ["git", "-C", str(bare_repo), "checkout", default_branch],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(bare_repo),
                "merge",
                "--no-ff",
                worktree_name,
                "-m",
                f"Merge {worktree_name} into {default_branch}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        # Remove worktree
        subprocess.run(
            ["git", "-C", str(bare_repo), "worktree", "remove", str(worktree_path)],
            check=True,
            capture_output=True,
            text=True,
        )

        # Delete the branch
        subprocess.run(
            ["git", "-C", str(bare_repo), "branch", "-d", worktree_name],
            check=True,
            capture_output=True,
            text=True,
        )

        # Update pool file
        if pool_file.exists():
            try:
                pool_data = yaml.safe_load(pool_file.read_text()) or {}
                if "worktrees" in pool_data:
                    pool_data["worktrees"].pop(worktree_name, None)
                pool_file.write_text(yaml.dump(pool_data, default_flow_style=False))
            except Exception as e:
                logger.warning("Failed to update pool file: %s", e)

        # Update main worktree
        main_worktree = project_dir / f"{project_name}-main"
        if main_worktree.is_dir():
            subprocess.run(
                ["git", "-C", str(main_worktree), "pull", "--ff-only"],
                capture_output=True,
                text=True,
            )

        await safe_reply(
            update.message,
            f"Merged `{worktree_name}` into `{default_branch}` and released worktree.",
        )

        # Trigger retrospective in the conversational session
        retro_prompt = (
            f"The worktree `{worktree_name}` has been merged to `{default_branch}`. "
            f"Run a retrospective following the guide in docs/retrospective.md. "
            f"Use /session-explorer to find the sessions that worked in this worktree, "
            f"reconstruct the timeline, and produce a retrospective document. "
            f"Write the output to work/retrospectives/{worktree_name}.md"
        )
        await session_manager.send_to_window(window_id, retro_prompt)

    except subprocess.CalledProcessError as e:
        await safe_reply(update.message, f"❌ Merge failed: {e.stderr or e.stdout}")


async def handle_conversational_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: object,
    chat_id: int,
    thread_id: int | None,
    text: str,
) -> None:
    """Handle a message in a conversational topic (or General).

    Creates a Claude Code session if the topic isn't bound yet, then forwards
    the message with the sender's name prefix.
    """
    from ..bot import _topic_names, _sanitize_tmux_name

    assert update.message is not None
    user_id = getattr(user, "id", 0)
    first_name = getattr(user, "first_name", "User")

    # Access control: dev group is DEV_USERS only
    if config.is_dev_group(chat_id) and not config.is_dev_user(user_id):
        await safe_reply(update.message, "❌ You don't have access to the dev group.")
        return

    wid = session_manager.get_window_for_topic(chat_id, thread_id)

    if not wid:
        # Need to create a conversational session
        project_name = config.project_for_group(chat_id)
        if not project_name:
            # Dev group -- no project mapping, use directory browser later
            # For now, respond that we need a project context
            await safe_reply(
                update.message,
                "❌ Dev group conversational topics are not yet implemented.",
            )
            return

        work_dir = _resolve_conversational_dir(project_name)
        topic_name = _topic_names.get((chat_id, thread_id or 0))
        tmux_name = f"{project_name}-chat"
        if topic_name:
            tmux_name = f"{project_name}-{_sanitize_tmux_name(topic_name)}"
        elif thread_id is None:
            tmux_name = f"{project_name}-general"

        logger.info(
            "Creating conversational session '%s' at %s (chat=%d, thread=%s)",
            tmux_name,
            work_dir,
            chat_id,
            thread_id,
        )

        # Check for resume_session_id from auto-restart
        resume_id = None
        if context.user_data:
            resume_id = context.user_data.pop("_conv_resume_session_id", None)

        success, message, created_name, created_wid = await tmux_manager.create_window(
            work_dir,
            window_name=tmux_name,
            allowed_tools="Read,Glob,Grep,Agent,WebSearch,WebFetch,LSP",
            resume_session_id=resume_id,
        )
        if not success:
            await safe_reply(update.message, f"❌ {message}")
            return

        # Longer timeout for --resume (slower to load)
        timeout = 15.0 if resume_id else 5.0
        await session_manager.wait_for_session_map_entry(created_wid, timeout=timeout)
        session_manager.bind_topic(
            chat_id,
            thread_id,
            created_wid,
            topic_type="conversational",
            window_name=created_name,
        )
        wid = created_wid

        # Instruct the session for conversational behavior.
        # Wait briefly for Claude to be ready, send instruction, then wait for
        # Claude to process it. Skip the instruction exchange in monitoring by
        # advancing the read offset past it.
        import asyncio as _asyncio

        await _asyncio.sleep(2.0)
        await session_manager.send_to_window(
            created_wid,
            "You are in a conversational Telegram topic with multiple users. "
            "Follow these guidelines:\n"
            "- When the conversation leads toward code changes, suggest $plan.\n"
            "- When the conversation drifts to a different subject, suggest "
            "$new <suggested-title> to move the discussion to a dedicated topic.\n"
            "- Keep responses conversational.\n"
            "- Do not respond to this message, just acknowledge silently and wait "
            "for the first real user message.",
        )
        # Give Claude time to process, then skip past this exchange
        await _asyncio.sleep(5.0)
        session = await session_manager.resolve_session_for_window(created_wid)
        if session and session.file_path:
            try:
                from pathlib import Path as _Path

                file_size = _Path(session.file_path).stat().st_size
                # Advance offset for all users so the instruction isn't forwarded
                for uid in config.allowed_users:
                    session_manager.update_user_window_offset(
                        uid, created_wid, file_size
                    )
            except OSError:
                pass

    # Handle $ commands in conversational topics
    if text.startswith("$"):
        cmd_parts = text.split(None, 1)
        cmd = cmd_parts[0].lower()
        cmd_arg = cmd_parts[1] if len(cmd_parts) > 1 else ""

        if cmd == "$plan":
            await _handle_plan_command(
                update, context, user_id, chat_id, thread_id, wid, cmd_arg
            )
            return
        if cmd == "$accept":
            await _handle_accept_command(
                update, context, user_id, chat_id, thread_id, wid, cmd_arg
            )
            return
        if cmd == "$new":
            await _handle_new_command(
                update, context, user_id, chat_id, thread_id, wid, cmd_arg
            )
            return
        if cmd == "$merge":
            await _handle_merge_command(
                update, context, user_id, chat_id, thread_id, wid, cmd_arg
            )
            return

    # Forward message with sender name prefix
    w = await tmux_manager.find_window_by_id(wid)
    if not w:
        # Session died — auto-restart with --resume to preserve context
        old_session = await session_manager.resolve_session_for_window(wid)
        resume_id = old_session.session_id if old_session else None
        session_manager.unbind_topic(chat_id, thread_id)
        # Store resume_id for the creation path
        if context.user_data is not None and resume_id:
            context.user_data["_conv_resume_session_id"] = resume_id
        await handle_conversational_message(
            update, context, user, chat_id, thread_id, text
        )
        return

    prefixed_text = f"[{first_name}] {text}"
    await update.message.chat.send_action(ChatAction.TYPING)
    success, msg = await session_manager.send_to_window(wid, prefixed_text)
    if not success:
        await safe_reply(update.message, f"❌ {msg}")
