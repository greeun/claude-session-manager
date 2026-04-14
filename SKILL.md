---
name: claude-session-manager
description: Track, triage, and navigate every concurrent Claude Code session on macOS. Use when the user asks about managing multiple Claude Code windows, listing tasks across terminals, finding which terminal window owns a session, prioritizing or marking sessions done, handling stale sessions, or invokes the `cst` command. Trigger words include cst, claude session manager, task list, tasks, register session, task register, task priority, task focus, stale sessions.
---

# Claude Session Manager (`cst`)

Sprint 1 delivers the core registry loop: discovery, listing, mutation,
hooks, and installer. Focus/resume, slash commands, the `watch` TUI,
statusline, and `review-stale`/`gc` arrive in later sprints.

## When to use

- The user has several Claude Code terminal windows and asks about them
  collectively ("which sessions are open?", "show my tasks").
- The user runs or mentions `cst`.
- The user asks to register, rename, prioritize, complete, or archive a
  session.

## Install (one-time)

```bash
cd <this skill dir>
bash install.sh
```

The installer:

- Creates `~/.local/bin/cst` pointing at `scripts/cst.py`.
- Creates `~/.claude/skills/claude-session-manager` symlink.
- Creates `~/.claude/claude-tasks/` (registry dir).
- Merges `SessionStart` and `UserPromptSubmit` hook entries into
  `~/.claude/settings.json` exactly once (full-string-equality match).
  Existing `statusLine` is never touched.
- If `~/.claude/settings.json` exists but is malformed JSON, the
  installer exits with code 2 and does NOT modify the file.
- Runs `cst list` as a smoke test.

Rerun the installer any time. It is idempotent.

## CLI (Sprint 1)

```
cst --version                       # cst 0.1.0
cst list                            # TSV: short_id priority status title project rel_time
cst list --all                      # include archived
cst list --json                     # JSON array in the same sort order
cst set <full-uuid> [--title ...] [--priority high|medium|low]
                    [--status ...] [--note ...] [--tags a,b]
cst done <full-uuid>
cst archive <full-uuid>
cst scan                            # scan ~/.claude/projects/, upsert drafts
cst hook session-start              # called by Claude Code SessionStart hook
cst hook activity                   # called by Claude Code UserPromptSubmit hook
```

Sort order: priority (high → medium → low), then `last_activity_at`
descending.

Session ids: Sprint 1 requires the full UUID. Short-id prefix matching
arrives in Sprint 2.

## Hook contract

Both hook subcommands parse a JSON payload on stdin FIRST
(`session_id`, `cwd`, `hook_event_name`, `transcript_path`). Env vars
(`CLAUDE_SESSION_ID`, `CLAUDE_PROJECT_DIR`, `PWD`) are used only as
fallback when stdin is empty, a TTY, or malformed. Hooks always exit
0; any failure is timestamped into
`~/.claude/claude-tasks/.hook-errors.log`.

## Registry on disk

One file per session at
`~/.claude/claude-tasks/<session-uuid>.json`. Writes are atomic
(tempfile + `os.replace`). Malformed files are renamed to
`<name>.json.corrupt-<unix-ts>` (bytes preserved) so siblings keep
working.

## Files in this skill

- `scripts/registry.py` — per-file JSON CRUD, atomic write, corrupt
  isolation, sorted listing.
- `scripts/scanner.py` — walks `~/.claude/projects/*/*.jsonl`, upserts
  draft records without ever overwriting user-owned fields.
- `scripts/hooks.py` — stdin-first hook entry points.
- `scripts/cst.py` — CLI dispatcher.
- `scripts/installer.py` — settings.json merge with malformed-file
  refusal (policy (a)).
- `install.sh` — user-facing installer.
- `tests/` — pytest suite (registry / scanner / hooks / cli /
  installer).

## What is NOT in Sprint 1

- `cst focus`, `cst resume`, AppleScript window binding.
- `cst watch` TUI.
- Statusline command and `/tasks`, `/task-register`, etc. slash
  commands.
- Live-vs-idle dot in list output.
- Stale detection, `cst review-stale`, `cst gc`.
- Short-id prefix matching with ambiguity handling.
- Stale-threshold config file.
- macOS platform guard (the code runs anywhere; only focus/resume in
  later sprints is macOS-specific).
