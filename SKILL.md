---
name: claude-session-manager
description: Track, triage, focus, and resume every concurrent Claude Code session on macOS. Use when the user asks about managing multiple Claude Code windows, listing tasks across terminals, finding which terminal window owns a session, jumping to that window, resuming a closed session, marking sessions done or stale, cleaning up archived records, or invokes the `cst` command. Trigger words include cst, claude session manager, task list, tasks, register session, task register, task priority, task focus, task resume, task statusline, stale sessions, review-stale, gc.
---

# Claude Session Manager (`cst`)

Sprint 2 delivers the daily-driver surface: progress capture
(last_user_prompt / last_assistant_summary / current_task_hint),
multi-line `cst list`, live-vs-idle dot, stale detection + triage
wizard, short-id prefix matching, `cst focus`/`cst resume`, statusline
command with installer wiring, macOS platform guard, `cst gc`, and a
config file for the stale threshold. Slash commands and the `watch`
TUI arrive in Sprint 3.

## When to use

- Several Claude Code terminal windows are open and the user asks
  collectively ("which sessions are open?", "show my tasks").
- The user runs or mentions `cst`.
- The user asks to jump to / focus / resume a session window.
- The user asks about stale or archived sessions.

## Install (one-time)

```bash
cd <this skill dir>
bash install.sh
```

The installer:

- Creates `~/.local/bin/cst` pointing at `scripts/cst.py`. Refuses to
  clobber a pre-existing regular file at that path.
- Creates `~/.claude/skills/claude-session-manager` symlink.
- Creates `~/.claude/claude-tasks/` (registry dir).
- Merges `SessionStart` and `UserPromptSubmit` hook entries into
  `~/.claude/settings.json` exactly once (full-string-equality match).
- Installs `"statusLine": {"type":"command", "command":"cst statusline", "padding":0}`
  **only when no statusLine already exists**. An existing value is
  never overwritten; a warning and integration instructions are
  printed instead.
- If `~/.claude/settings.json` exists but is malformed JSON, the
  installer exits with code 2 and does NOT modify the file.
- Runs `cst list` as a smoke test.

Rerun the installer any time. It is idempotent.

## CLI (Sprint 2)

```
cst --version                       # cst 0.2.0

cst list                            # multi-line: headline + ⤷ prompt + ⚙ hint
cst list --compact                  # one line per session (CI/pipelines)
cst list --all                      # include archived
cst list --stale                    # only stale rows
cst list --json                     # JSON array with all progress + live keys

cst set <id|prefix>  [--title ...] [--priority high|medium|low]
                     [--status in_progress|blocked|waiting|done]
                     [--note ...] [--tags a,b]
cst done    <id|prefix>
cst archive <id|prefix>

cst focus   <id|prefix>             # bring terminal to front (macOS only)
cst resume  <id|prefix>             # new window + `claude --resume <id>`
cst gc                              # delete records archived > 7 days ago
cst review-stale                    # interactive keep/done/archive/skip
cst statusline                      # compact pending/stale summary

cst scan                            # scan ~/.claude/projects/, upsert drafts
cst hook session-start              # called by Claude Code SessionStart hook
cst hook activity                   # called by Claude Code UserPromptSubmit hook
```

**Short-id prefix matching.** Every id-taking subcommand accepts a
full UUID or a prefix ≥ 6 hex characters. Too-short prefixes exit 2;
ambiguous prefixes print candidates and exit 3 without mutating
anything; not-found exits 1.

**Sort order.** Priority (high → medium → low), then
`last_activity_at` descending.

**Display.** The headline row starts with a live-vs-idle dot
(`●` live / `○` idle), followed by short id, priority, status,
title, project, relative time. Two optional dim sub-rows follow:
`⤷ <last user prompt>` (U+2937) and `⚙ <current task hint>` (U+2699).
When any session is stale, a footer banner
`⚠ N stale sessions — run 'cst review-stale'` appears. `--compact`
omits all sub-rows and the banner; `--json` omits all non-JSON chrome.

## Progress capture

Scanner-owned fields are always refreshed from the transcript tail
(last 50 JSONL lines). Users cannot write them. The scanner honors a
"fresher wins" rule: the `UserPromptSubmit` hook writes
`last_user_prompt` directly, bumping `last_activity_at`; the scanner
only overwrites the prompt when the JSONL mtime is strictly newer
than the stored `last_activity_at` AND the extracted value differs.

`current_task_hint` is derived from the most recent `tool_use` block:

- `Bash` with `command` → `Running: <command>`
- `Edit`/`Write`/`MultiEdit`/`NotebookEdit` with `file_path` → `Editing: <relpath-or-basename>`
- `Read` with `file_path` → `Reading: <relpath-or-basename>`
- `Grep`/`Glob` with `pattern` → `Searching: <pattern>`
- recognised tool with no distinguishing input → bare tool name
- otherwise: empty (no sub-row rendered)

All three fields are truncated on code-point boundaries to 100 chars
with a trailing `…` when shortened. No external AI call is ever made.

## Stale threshold config

Optional: `~/.claude/claude-tasks.config.json`:

```json
{ "stale_threshold_seconds": 3600 }
```

Malformed / zero / negative / non-int values are ignored (logged to
`~/.claude/claude-tasks/.scanner-errors.log`) and the default 14400s
(4 hours) is used. `CST_STALE_THRESHOLD_SECONDS` (env) overrides the
file (used by tests).

## Hook contract

Both hook subcommands parse a JSON payload on stdin FIRST
(`session_id`, `cwd`, `hook_event_name`, `transcript_path`, plus
`prompt` or `user_prompt` for `UserPromptSubmit`). Env vars
(`CLAUDE_SESSION_ID`, `CLAUDE_PROJECT_DIR`, `PWD`) are used only as
fallback. Hooks always exit 0; any failure is timestamped into
`~/.claude/claude-tasks/.hook-errors.log`. When `cst hook activity`
fires for an unknown `session_id`, a skeleton record is created so
the session shows up immediately.

## macOS platform guard

`cst focus` and `cst resume` require macOS. On any non-darwin
platform they exit 6 with a clear message. All other subcommands
work on any platform (live-dot degrades silently to `○` when `ps`
misbehaves).

## Files in this skill

- `scripts/registry.py` — per-file JSON CRUD, atomic write, corrupt
  isolation, progress field defaults, `update` refuses progress fields.
- `scripts/scanner.py` — transcript tail extraction with "fresher wins".
- `scripts/hooks.py` — stdin-first hook entry points, UserPromptSubmit
  writes `last_user_prompt` + create-skeleton for unknown id.
- `scripts/cst.py` — CLI dispatcher, multi-line list, stale/live
  display.
- `scripts/resolver.py` — short-id prefix resolver.
- `scripts/livedot.py` — `ps -o pid,tty,comm` parsing.
- `scripts/focus.py` — iTerm2 / Terminal.app AppleScript templates.
- `scripts/resume.py` — new-window spawn with shell + AppleScript
  escape layers.
- `scripts/statusline.py` — read-only pending/stale count.
- `scripts/review_stale.py` — interactive keep/done/archive/skip.
- `scripts/cst_gc.py` — archived > 7 day deletion.
- `scripts/config.py` — stale threshold loader.
- `scripts/platform_macos.py` — macOS guard with test override.
- `scripts/installer.py` — settings.json merge, statusline wiring.
- `install.sh` — user-facing installer.
- `tests/` — pytest suite (162 tests across 12 files).

## What is NOT in Sprint 2

- Slash commands (`/tasks`, `/task-register`, etc.).
- `cst watch` TUI.
- `cst watch --pin` dedicated window.

These arrive in Sprint 3.
