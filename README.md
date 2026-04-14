# claude-session-manager (`csm`)

Track, triage, focus, and resume every concurrent Claude Code session on macOS.

When you have five Claude Code windows open across three projects, `csm` gives you
one command to see them all, jump to the right terminal, and resume any session that
was closed.

```
● 3f9a2c  [high]  in_progress  refactor auth middleware        ~/src/api       2m ago
  ⤷ can you split the token verifier into its own module?
  ⚙ Editing: src/auth/verify.ts
○ 7b1e40  [med]   waiting      add i18n sync skill             ~/skills        18m ago
  ⤷ wire the CLI to detect next-intl automatically
  ⚙ Running: pytest tests/
⚠ 1 stale sessions — run 'csm review-stale'
```

## Features

- **Live session registry** — every Claude Code session is auto-registered via
  `SessionStart` / `UserPromptSubmit` hooks; no manual bookkeeping.
- **Progress at a glance** — last user prompt and current tool activity
  (`Editing: foo.ts`, `Running: pytest`) rendered under each row.
- **Live vs. idle dot** — `●` when the Claude process is still running, `○` when
  the window is idle or closed.
- **Priority + status triage** — tag sessions `high`/`medium`/`low` and
  `in_progress`/`blocked`/`waiting`/`done`.
- **Stale detection** — sessions with no activity past the configured threshold
  (default 4h) are flagged; `csm review-stale` walks you through keep/done/archive.
- **Focus & resume** — `csm focus <id>` brings the owning terminal window to the
  front; `csm resume <id>` opens a fresh window and runs `claude --resume <id>`.
  Supports iTerm2, Terminal.app, WezTerm, Kitty, and Ghostty.
- **Statusline integration** — compact pending/stale summary in the Claude Code
  statusline.
- **Slash commands** — `/tasks`, `/task-register`, `/task-focus`, `/task-done`,
  `/done`, `/task-priority`, `/task-note`, `/task-status` work from inside any
  Claude Code session.

## Requirements

- macOS (focus/resume use AppleScript / terminal CLIs; registry + list work anywhere)
- Python 3.9+
- Claude Code

## Install

```bash
git clone <this-repo>
cd claude-session-manager
bash install.sh
```

The installer is idempotent. It:

1. Symlinks `~/.local/bin/csm` → `scripts/csm.py`.
2. Symlinks `~/.claude/skills/claude-session-manager` → this directory.
3. Symlinks `commands/*.md` → `~/.claude/commands/` (slash commands).
4. Creates `~/.claude/claude-tasks/` (the registry directory).
5. Merges `SessionStart` and `UserPromptSubmit` hooks into `~/.claude/settings.json`
   (never duplicates; never overwrites unrelated entries).
6. Installs `csm statusline` as your Claude Code statusline **only if** you don't
   already have one configured.
7. Runs `csm list` as a smoke test.

Make sure `~/.local/bin` is on your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Quick Start

Open a couple of Claude Code windows, then in any terminal:

```bash
csm list                   # see every session
csm set 3f9a2c --priority high --title "refactor auth"
csm focus 3f9a2c           # jump to that window
csm resume 7b1e40          # reopen a closed session in a new window
csm review-stale           # interactive triage for stale sessions
```

Short-id prefix matching works everywhere — 6+ hex chars is enough. Ambiguous
prefixes print the candidates and exit without mutating anything.

## Command Reference

```
csm list                            multi-line: headline + ⤷ prompt + ⚙ hint
csm list --compact                  one line per session
csm list --all                      include archived
csm list --stale                    only stale rows
csm list --json                     machine-readable output

csm current                         print the current session id (uses
                                    CLAUDE_SESSION_ID or infers from tty)

csm set <id> [--title ...] [--priority high|medium|low]
            [--status in_progress|blocked|waiting|done]
            [--note ...] [--tags a,b]
csm done    <id>                    mark done
csm archive <id>                    archive (hidden from default list)

csm focus   <id>                    bring terminal window to front (macOS)
csm resume  <id>                    new window running `claude --resume <id>`

csm watch                           live TUI (updates as sessions change)
csm watch --pin                     pin watch to its own dedicated window

csm review-stale                    interactive keep/done/archive/skip
csm gc                              delete records archived > 7 days ago
csm statusline                      compact pending/stale summary
csm scan                            rescan ~/.claude/projects/ transcripts

csm --version
```

### Exit codes for id resolution

| Code | Meaning |
|------|---------|
| 1 | Not found |
| 2 | Prefix shorter than 6 hex characters |
| 3 | Ambiguous prefix (candidates printed to stderr) |
| 6 | Subcommand requires macOS |

## Slash Commands

Available inside any Claude Code session (installed automatically):

| Command | Purpose |
|---------|---------|
| `/tasks` | List all tracked sessions |
| `/task-register` | Register the current session with title and priority |
| `/task-focus <id>` | Focus another session's terminal window |
| `/task-priority <level>` | Set priority on the current session |
| `/task-status <status>` | Set status on the current session |
| `/task-note <text>` | Attach a note to the current session |
| `/task-done`, `/done` | Mark the current session done |

Slash commands auto-fall back to `csm current` when `CLAUDE_SESSION_ID` isn't set,
so they work in older Claude Code versions too.

## Progress Capture

`csm` extracts three fields from each session's transcript tail (last 50 JSONL
lines) — no AI calls, no network:

- **`last_user_prompt`** — most recent user message (bumped immediately by the
  `UserPromptSubmit` hook; scanner only overwrites when transcript mtime is
  strictly newer).
- **`last_assistant_summary`** — most recent assistant reply.
- **`current_task_hint`** — derived from the latest `tool_use` block:
  - `Bash` → `Running: <command>`
  - `Edit`/`Write`/`MultiEdit` → `Editing: <path>`
  - `Read` → `Reading: <path>`
  - `Grep`/`Glob` → `Searching: <pattern>`

All three are truncated to 100 code-points with a trailing `…`.

## Configuration

Optional `~/.claude/claude-tasks.config.json`:

```json
{ "stale_threshold_seconds": 3600 }
```

Default is `14400` (4 hours). Invalid values fall back to the default and are
logged to `~/.claude/claude-tasks/.scanner-errors.log`. Env var
`CST_STALE_THRESHOLD_SECONDS` overrides the file (used by the test suite).

## How It Works

```
Claude Code session
  │
  ├── SessionStart hook        → csm hook session-start   (registers record)
  └── UserPromptSubmit hook    → csm hook activity        (bumps last_activity_at,
                                                           writes last_user_prompt)

~/.claude/claude-tasks/
  └── <session-uuid>.json      one file per session, atomic writes

csm scan                       reads ~/.claude/projects/*/transcripts/*.jsonl
                               tails for progress fields ("fresher wins")

csm list / watch / statusline  read-only views over the registry
csm focus / resume             AppleScript / terminal CLIs for window control
```

Hooks always exit 0; any failure is timestamped into
`~/.claude/claude-tasks/.hook-errors.log` so a broken hook never blocks Claude
Code itself.

## Platform Support

| Subcommand | macOS | Linux | Windows |
|---|---|---|---|
| `list`, `set`, `done`, `archive`, `scan`, `statusline`, `gc`, `review-stale`, `watch` | ✓ | ✓ | ✓ |
| `focus`, `resume` | ✓ | exit 6 | exit 6 |

The live-vs-idle dot degrades gracefully to `○` if `ps` output can't be parsed.

## Uninstall

```bash
rm ~/.local/bin/csm
rm ~/.claude/skills/claude-session-manager
rm ~/.claude/commands/{tasks,task-*,done}.md
# Remove SessionStart / UserPromptSubmit entries from ~/.claude/settings.json
# Optionally delete the registry:
rm -rf ~/.claude/claude-tasks
```

## License

MIT
