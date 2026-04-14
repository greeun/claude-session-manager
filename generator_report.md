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

## Post-Sprint 1 polish

Applied the two non-blocking polish fixes from `critique.md`.

### Fix 1 — `cst set --status` enum validation

Added enum validation mirroring the existing `--priority` check.
Allowed values: `in_progress | blocked | waiting | done`. On any
other value the CLI prints
`cst: --status must be in_progress|blocked|waiting|done` to stderr,
exits 1, and does NOT mutate the record.

Code change: `scripts/cst.py` inside `cmd_set`.

New tests in `tests/test_cli.py`:

- `test_set_status_rejects_invalid_value` — asserts exit 1, stderr
  message, AND the stored `status` + `auto_detected` are unchanged.
- `test_set_status_accepts_all_valid_values` — parametrized over
  all four valid values; each must exit 0 and persist.

### Fix 2 — `install.sh` refuses to clobber a regular file

Added an up-front guard before any filesystem mutation. If
`~/.local/bin/cst` exists and is NOT a symlink, the installer
prints
`cst install: <path>/cst exists as a regular file; refusing to overwrite. Remove it or move it aside, then rerun.`
to stderr, exits 3, and performs no other disk writes (no
`mkdir`, no symlinks, no `settings.json` creation).

Existing symlinks at that path are still replaced idempotently via
`ln -sfn`.

Code change: `install.sh` — guard moved ahead of the `mkdir -p`
block so a refused install is a true no-op.

New tests in `tests/test_installer.py`:

- `test_install_refuses_when_cst_bin_is_regular_file` — asserts
  non-zero exit, the stderr message, the regular file's bytes are
  unchanged, it is still a regular file (not a symlink), and
  `~/.claude/settings.json` was NOT created as a side effect.
- `test_install_replaces_existing_symlink` — complements the
  above; asserts the idempotent re-install path still works when
  the existing entry is a symlink (even a stale/broken one).

### Verification

Full pytest run after both fixes:

```
...........................................                              [100%]
43 passed in 2.19s
```

Delta from Sprint 1 baseline (36 → 43): +2 status validation tests
(one parametrized into 4 cases is counted by pytest as 4) + 2 bin
symlink tests + some refactor counts. Breakdown verified:
6 registry + 9 scanner + 7 hooks + 13 CLI + 8 installer = 43.

Manual sandbox re-verification:

```
=== Check 1: idempotency ===
CHECK_1_PASS
=== Check 10: installer preserves existing statusLine ===
CHECK_10_PASS
=== Probe 4: cst set --status rejects bogus value ===
exit=1
stderr: cst: --status must be in_progress|blocked|waiting|done
stored status=in_progress
PROBE_4_PASS
=== Probe 12: install.sh refuses regular file at ~/.local/bin/cst ===
cst install: <tmp>/.local/bin/cst exists as a regular file; refusing to overwrite. Remove it or move it aside, then rerun.
exit=3
is_symlink_now=no bytes_unchanged=yes settings_created=no
PROBE_12_PASS
```

Updated `git log --oneline`:

```
fe2f83b install.sh: refuse to overwrite regular file at ~/.local/bin/cst
a8b1e17 cst set: reject invalid --status values (enum: in_progress|blocked|waiting|done)
de53c21 Sprint 1 handoff: generator_report.md
e97516a Sprint 1 tests (36 passing) + SKILL.md
cd037de Sprint 1 core: registry, scanner, hooks, cst CLI, installer
c0936b3 Sprint 1 kickoff: spec + brainstorm + sprint_contract in place
```
