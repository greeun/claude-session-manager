# Sprint 1 Report

## Features implemented (from sprint contract §2)

- **Registry** (`scripts/registry.py`): per-session JSON files under
  `~/.claude/claude-tasks/`; atomic writes via tempfile + `os.replace`;
  corrupt files renamed to `<name>.json.corrupt-<unix_ts>` with bytes
  preserved; sorted listing by priority (high→medium→low) then
  `last_activity_at` desc; `CST_REGISTRY_DIR` override for tests.
- **Scanner** (`scripts/scanner.py`): walks
  `~/.claude/projects/*/*.jsonl`, UUID-only filenames, title seeded
  from first `user` line (falls back to `project_name`), project-slug
  decode heuristic, never overwrites user-owned fields once
  `auto_detected=false`, never archives/deletes.
- **Hooks** (`scripts/hooks.py`): `session-start` and `activity`
  parse stdin JSON first (`session_id`, `cwd`, `transcript_path`),
  fall back to env vars (`CLAUDE_SESSION_ID`, `CLAUDE_PROJECT_DIR`,
  `PWD`); always exit 0; errors timestamped to
  `~/.claude/claude-tasks/.hook-errors.log`.
- **CLI** (`scripts/cst.py`): `list` (with `--all`, `--stale`,
  `--json`), `set` (title/priority/status/note/tags), `done`,
  `archive`, `scan`, `hook {session-start|activity}`, `--version`
  (`cst 0.1.0`).
- **Installer** (`install.sh` + `scripts/installer.py`): symlinks
  `~/.local/bin/cst` and `~/.claude/skills/claude-session-manager`,
  creates `~/.claude/claude-tasks/`, merges hooks into
  `~/.claude/settings.json` with full-string-equality matching,
  exits 2 on malformed settings without modifying the file (policy
  (a)), smoke-tests with `cst list`.
- **SKILL.md**: YAML frontmatter (`name`, `description` with trigger
  keywords), 105 lines.

DoD bullets covered (Sprint 1 slice): installation/wiring, detection
and registration, user-field stickiness, `cst list` sort + empty
behavior, `done`/`archive` lifecycle, corrupt-file isolation, hook
non-blocking, subcommand non-zero on error.

## How to run

```bash
# one-time install (safe to rerun; idempotent):
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-manager
bash install.sh

# ensure ~/.local/bin is on PATH (the installer prints this if missing):
export PATH="$HOME/.local/bin:$PATH"

# sandboxed install (what the evaluator should use for probing):
TMPH=$(mktemp -d)
HOME=$TMPH bash install.sh
HOME=$TMPH PATH="$TMPH/.local/bin:$PATH" cst list

# scan the user's projects tree:
cst scan

# simulate a Claude Code session without running claude:
CLAUDE_SESSION_ID=<uuid> CLAUDE_PROJECT_DIR=/path/to/proj cst hook session-start < /dev/null
# OR stdin-payload form (recommended; what Claude Code actually uses):
echo '{"session_id":"<uuid>","cwd":"/path","hook_event_name":"SessionStart","transcript_path":"/x.jsonl"}' \
    | cst hook session-start

# mutate / lifecycle:
cst set <full-uuid> --title "Login refactor" --priority high
cst done <full-uuid>
cst archive <full-uuid>
cst list           # default (active only)
cst list --all     # include archived
cst list --json    # machine-checkable
```

Required env for tests only: nothing; conftest sets `HOME` and
`CST_REGISTRY_DIR` to a pytest tmp dir.

Optional env for scanner: `CST_PROJECTS_DIR` to point the scanner at
something other than `~/.claude/projects`.

## Seed data / test accounts

No external accounts or network. Tests construct fixtures with:

- Synthetic UUIDs like `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa`.
- Inline JSONL lines written by the test into `CST_PROJECTS_DIR`.
- Direct record seeding via `registry.write(registry.new_record(...))`.

No real `~/.claude/projects/` data is read by any test (conftest
redirects `HOME` for every test).

## Known limitations

- **Project-slug decoding is a heuristic** (risk §10.8). The binding
  test cases from amendment #11 force a non-mechanical rule: for
  slugs with ≤3 dash-separated components after the leading dash,
  we take the last two joined by `-` (so `-tmp-fake-proj` →
  `fake-proj`); for longer slugs we take the last component (so
  `-Users-alice-proj-foo` → `foo`). Real user paths like
  `-Users-name-long-proj-name` can decode to the wrong segment. This
  only affects the cosmetic `project_name`; `cwd` captured from
  hooks/JSONL is authoritative.
- **No short-id prefix matching in Sprint 1**. Every command that
  takes a session id requires the full 36-char UUID. The contract
  explicitly defers this to Sprint 2.
- **No stale detection, statusline, focus, resume, watch TUI, slash
  commands, or gc** — all deferred per contract §3.
- **Title seeding assumes UTF-8 JSONL**. A non-UTF-8 transcript falls
  back silently to `project_name`.
- **Concurrency beyond distinct records is untested**. Same-record
  concurrent writes from two processes are not serialized (no fcntl
  locks in Sprint 1). Within a single process the `os.replace`
  atomicity is enough; across processes, last writer wins. This is
  acceptable for the hook+scanner+CLI pattern where a single session
  id is rarely written by two processes simultaneously.
- **`test_activity_hook_touches_last_activity_at` sleeps ~1.05s** to
  guarantee the ISO second-precision timestamp advances. Not flaky in
  practice but adds ~1s to the suite.

## Verification I already performed

### `pytest -q tests/`

```
....................................                                     [100%]
36 passed in 1.96s
```

Breakdown: 6 registry + 9 scanner + 7 hooks + 8 CLI + 6 installer.

### §5 observable checks (sandboxed `HOME=$(mktemp -d)`)

Ran `/tmp/run_checks.sh` which executes every check from
sprint_contract.md §5 end-to-end.

```
CHECK 1:  PASS -- installer idempotent; exact-string hook match
CHECK 2:  PASS -- cst list empty -> exit 0, prints '(no sessions)'
CHECK 3:  PASS -- scanner creates record with title, auto_detected, archived=false
CHECK 4:  PASS -- scanner respects title/priority/status/note/tags after user edit
CHECK 5:  PASS -- corrupt isolated; bytes preserved; sibling row renders
CHECK 6:  PASS -- hooks exit 0; log has fresh timestamped line
CHECK 7:  PASS -- list --json sort order = high(recent,old), medium, low
CHECK 8:  PASS -- pytest atomic_write_no_partial
CHECK 9:  PASS -- done visible=1, after archive visible=0, --all=1
CHECK 10: PASS -- installer leaves existing statusLine untouched
CHECK 11: PASS -- two distinct session ids -> 2 files, 2 rows
CHECK 12: PASS -- installer from pristine HOME creates settings.json with both events
CHECK 13: PASS -- installer exits nonzero; file byte-identical; no backup
CHECK 14: PASS -- stdin-only hook creates record with payload values

=== SUMMARY: 14 pass, 0 fail ===
```

### `git log --oneline`

```
e97516a Sprint 1 tests (36 passing) + SKILL.md
cd037de Sprint 1 core: registry, scanner, hooks, cst CLI, installer
c0936b3 Sprint 1 kickoff: spec + brainstorm + sprint_contract in place
```
