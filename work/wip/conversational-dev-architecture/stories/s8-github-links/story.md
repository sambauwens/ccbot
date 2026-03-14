# S8: GitHub Links in Conversational Topics

## Context

From the core vision (Message 3, 07:23 UTC):

> conversational topics [...] where you also give us links to the content (mostly the md files) in github so we can read them there (we nathalie and me both have access to github)

From the ExitPlanMode rejection #2:

> "GitHub link" => always link directly to the right header where there is one that matches.

## Current Implementation

### Helpers
- `_parse_github_url(remote_url)` — parses GitHub HTTPS URL from git remote (HTTPS or SSH format)
- `_make_github_link(base_url, file_path, branch, heading)` — builds blob link with optional header anchor

### Post-processing
- `_inject_github_links(text, work_dir)` — scans Claude's response text for file paths (regex-based), resolves them relative to the working directory, replaces with `[filename](github-link)` markdown
- Wired into `handle_new_message` for conversational topics only (not dev topics)
- Runs `git remote get-url origin` to get the remote URL

### Limitations
- Regex-based file path detection — may miss some paths or match false positives
- Only processes .md, .py, .ts, .js, .yml, .yaml, .json, .toml extensions
- Header anchor matching not yet implemented (links to file, not specific heading)
- `subprocess.run` called per message to get remote URL (could be cached)

## Tasks

| # | Task | Status |
|---|------|--------|
| T1 | Parse GitHub URL from git remote | done |
| T2 | Build links with header anchors | done (helper exists) |
| T3 | Post-process conversational responses | done |
| T4 | Actually match headers in referenced files to generate anchor links | not done — _make_github_link accepts a heading but _inject_github_links never passes one |
| T5 | Cache git remote URL per project (avoid subprocess per message) | not done |
| T6 | Test with real conversational messages | not done |

## Acceptance Criteria

- When Claude references a file path in a conversational topic, a clickable GitHub link appears
- For .md files, the link points to the specific heading when a matching anchor exists
- Links are only injected for files that actually exist in the project
- No GitHub links in dev topic messages (only conversational)
- The remote URL is cached, not fetched per message
