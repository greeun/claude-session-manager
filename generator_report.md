# Sprint 2 Report

## Features implemented (from sprint contract §2)

- **Progress capture** (`scripts/scanner.py` + `scripts/hooks.py`):
  three scanner-owned fields (`last_user_prompt`,
  `last_assistant_summary`, `current_task_hint`). Scanner streams the
  last 50 non-empty JSONL lines; extracts first user line, first
  assistant text parts (tool_use skipped), and last `tool_use` block.
  Tool-hint builders: `Bash`→`Running:`, `Edit|Write|MultiEdit|NotebookEdit`→`Editing:`,
  `Read`→`Reading:`, `Grep|Glob`→`Searching:`; bare name when input
  missing; empty string when no tool_use in tail or name missing.
  100-char code-point truncation with `"…"` suffix. Non-UTF-8 bytes
  handled via `errors="replace"`. "Fresher wins" rule (option b):
  scanner only overwrites `last_user_prompt` when JSONL mtime is
  strictly newer than record's `last_activity_at` AND extracted value
  is non-empty AND differs from stored value.
- **Hook enhancements** (`scripts/hooks.py`): `cst hook activity`
  parses `prompt` or `user_prompt` from stdin JSON (both variants per
  contract §10.1), truncates to 100 chars, writes to
  `last_user_prompt`. Creates skeleton record for unknown `session_id`
  (option a per §2.3). Always exits 0.
- **Registry extension** (`scripts/registry.py`): `new_record()`
  seeds all three progress fields to `""`. `read()` and
  `iter_records()` backfill missing progress fields for pre-Sprint-2
  records. `update()` raises `ValueError` on any progress field key
  (defense in depth).
- **Multi-line `cst list`** (`scripts/cst.py` `cmd_list`): headline
  row (tab-separated: dot, short_id, priority, status, title, project,
  relative time) + optional dim sub-rows `⤷ <prompt>` (U+2937) and
  `⚙ <hint>` (U+2699). Empty sub-rows omitted entirely.
  `--compact` strips all sub-rows and the stale banner.
  `--json` extended with `last_user_prompt`, `last_assistant_summary`,
  `current_task_hint`, `live` keys; no banner in JSON output; all
  Sprint 1 keys preserved.
- **Live-vs-idle dot** (`scripts/livedot.py`): shells out to
  `ps -o pid,tty,comm -A`, parses via `split(None, 2)`, matches by
  `os.path.basename(comm) == "claude"`, normalises `ttysNNN` /
  `pts/N` / `?`/`??`/`-`. Silent degrade (empty set + log line) on
  `ps` failure.
- **Stale detection** (`scripts/cst.py` `_is_stale`,
  `scripts/statusline.py` shared predicate): derived, never stored.
  Display swaps status → `stale` without mutating the record. Banner
  `⚠ N stale sessions — run 'cst review-stale'` on any non-empty
  stale set (except under `--json`).
- **`cst review-stale`** (`scripts/review_stale.py`): interactive
  loop, one session per prompt in priority → recency order. Accepts
  `k/keep`, `d/done`, `a/archive`, `s/skip` (case-insensitive).
  Non-interactive (EOF) treats remaining as skip. 3 bad answers →
  defensive skip. keep/skip are byte-identical no-ops.
- **Short-id prefix resolver** (`scripts/resolver.py`):
  `TOO_SHORT` (exit 2), `NOT_FOUND` (exit 1), `AMBIGUOUS` (exit 3
  with candidate list in priority → recency order), `OK` otherwise.
  Full UUID always bypasses the prefix path. Applied to `set`,
  `done`, `archive`, `focus`, `resume`.
- **`cst focus`** (`scripts/focus.py`): iTerm2 / Terminal.app
  AppleScript templates pinned verbatim per §2.8. `window_id` forced
  through strict `int()` (rejects str / bool / dict → exit 5 with
  "corrupt window_id"). Unsupported app → exit 4 with `cst resume`
  hint. osascript non-zero → exit 5 with hint.
- **`cst resume`** (`scripts/resume.py`): two escape layers —
  `shlex.quote()` for cwd + session_id in the shell command, then
  `"` / `\` escaping for the AppleScript string literal wrap. cwd
  with newline or null byte → exit 1. iTerm2 preferred; Terminal.app
  fallback; neither installed → exit 4.
- **macOS platform guard** (`scripts/platform_macos.py`):
  `sys.platform == "darwin"` check with `CST_FORCE_PLATFORM` test
  override. `cst focus` and `cst resume` exit 6 on non-darwin. All
  other subcommands platform-agnostic.
- **`cst statusline`** (`scripts/statusline.py`): read-only
  counts from the registry. Emits `📋 <N> pending  →  /tasks` or
  `📋 <N> pending · <M> stale  →  /tasks` or an empty line when
  nothing pending. No subprocess, no network, no JSONL parsing.
  Perf-tested: ≤ 150ms on 200 records.
- **`cst gc`** (`scripts/cst_gc.py` — renamed from `gc.py` to avoid
  stdlib collision): deletes only records where `archived is True`
  AND `archived_at` is parseable ISO-8601-Z AND older than 7 days.
  Uses stored `archived_at`, never file mtime. Partial unlink
  failure → exit 1 after full pass. Unparseable `archived_at` →
  skip + log. Empty summary format:
  `cst gc: deleted N record(s); kept M archived record(s) still within the 7-day window`.
- **Stale threshold config** (`scripts/config.py`): loads
  `~/.claude/claude-tasks.config.json` with strict validation.
  Rejects non-int (bool explicitly rejected via `type(v) is int`),
  zero, and negative values. Warning line names the offending key.
  Precedence: `CST_STALE_THRESHOLD_SECONDS` env > config > default
  (14400s).
- **Installer statusline wiring** (`scripts/installer.py`): sets
  `{"type":"command","command":"cst statusline","padding":0}` only
  when no `statusLine` exists. Existing key preserved with integration
  guidance on stdout containing literal `existing statusline`.
  Idempotent. Malformed `settings.json` policy (a) retained from
  Sprint 1 (exit 2, no modification).

## How to run

```bash
# one-time install (idempotent; picks up Sprint 2 changes):
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-manager
bash install.sh
export PATH="$HOME/.local/bin:$PATH"

cst --version                           # cst 0.2.0

# progress + multi-line list:
cst list                                # headline + ⤷ prompt + ⚙ hint
cst list --compact                      # one line per session
cst list --stale                        # only stale rows
cst list --json                         # machine-checkable

# short-id prefix on any id-taking subcommand:
cst set abcdef --priority high          # 6-char prefix
cst done abcdef12                       # 8-char prefix
cst archive <full-uuid>

# focus / resume (macOS):
cst focus abcdef                        # bring window to front
cst resume abcdef                       # new window + claude --resume

# stale triage + cleanup:
cst review-stale                        # interactive; EOF = skip remaining
cst gc                                  # delete archived > 7 days

# statusline (fast, registry-read-only):
cst statusline

# optional config file:
echo '{"stale_threshold_seconds": 3600}' > ~/.claude/claude-tasks.config.json

# sandbox for evaluator:
TMPH=$(mktemp -d); HOME=$TMPH bash install.sh
HOME=$TMPH PATH="$TMPH/.local/bin:$PATH" cst list
```

Env vars (tests): `CST_REGISTRY_DIR`, `CST_PROJECTS_DIR`,
`CST_CONFIG_PATH`, `CST_STALE_THRESHOLD_SECONDS`, `CST_FORCE_PLATFORM`.

## Known limitations

- **`ps` live-dot is best-effort**. On macOS `ps -o pid,tty,comm -A`
  truncates long paths at column width; we match by
  `os.path.basename(comm) == "claude"`, so a process launched as
  `node /path/claude-cli.js` (basename `node`) is NOT matched even
  though a human might call it "claude". Contract is explicit about
  this.
- **Window focus for iTerm2 without a stored `window_id`** degrades
  to bare `activate` (Sprint 1 hook capture does not populate
  window_id reliably). `cst resume` is the working fallback and all
  focus-failure paths point there.
- **AppleScript is inlined and not signed**. On machines where the
  user has not granted osascript permissions for Terminal Automation,
  `cst focus` / `cst resume` will fail with exit 5 (surfaces the
  AppleScript non-zero to the user with the resume hint).
- **Project-slug decoding heuristic** (inherited from Sprint 1): see
  prior report; no new behavior in Sprint 2.
- **No actual AppleScript execution in CI**. `_run_osascript` is
  monkeypatched in all tests; templates are byte-pinned but the real
  AppleScript parse on macOS is not verified by the harness.
- **Scanner `_seed_from_jsonl` opens with `errors="replace"`** so a
  non-UTF-8 transcript does not crash scanning. The replacement char
  U+FFFD may therefore appear in titles or `last_user_prompt` when a
  transcript has bad bytes.

## Verification I already performed

### `pytest -q tests/`

```
........................................................................ [ 44%]
........................................................................ [ 88%]
..................                                                       [100%]
162 passed in 5.69s
```

Breakdown: Sprint 1 tests remain green (9 registry + 33 scanner +
13 hooks + 35 CLI + 10 installer = 100 in the Sprint-1-shaped files,
extended). Plus 7 new files: focus (9) + resume (12) + live_dot (7)
+ gc (5) + review_stale (8) + statusline (7) + config (14) = 62.
Total 162.

### §5 observable checks (sandboxed `HOME=$(mktemp -d)`)

Ran `/tmp/run_sprint2_checks.sh`:

```
CHECK 1: PASS -- scanner extracts all three progress fields
CHECK 2: PASS -- scanner overwrites progress; user fields preserved
CHECK 3: PASS -- truncation: 100 chars with single …
CHECK 4: PASS -- CJK code-point truncation
CHECK 5: PASS -- non-UTF-8 bytes do not crash
CHECK 6: PASS -- current_task_hint variants (pytest proxy)
CHECK 7: PASS -- multi-line list renders ⤷/⚙
CHECK 8: PASS -- --compact: no sub-rows, one row per session
CHECK 9: PASS -- hook writes last_user_prompt from stdin
CHECK 10: PASS -- stale banner + --stale filter + stored status unchanged
CHECK 11: PASS -- review-stale: keep/skip byte-identical; 3-session priority order
CHECK 12: PASS -- ambiguous prefix: exit 3, candidates, no mutation
CHECK 12b: PASS -- ambiguous prefix on every id-taking subcommand
CHECK 13: PASS -- short prefix: exit 2
CHECK 14: PASS -- focus/resume pytest coverage
CHECK 15: PASS -- focus on non-macOS exits 6
CHECK 16: PASS -- statusline output shapes
CHECK 17: PASS -- statusline perf ≤ 150ms on 200 records
CHECK 18: PASS -- installer statusline: fresh/preserve/idempotent with full shape
CHECK 19: PASS -- config drives stale threshold
CHECK 20: PASS -- malformed config does not break cst list
CHECK 21: PASS -- gc respects 7-day window + pinned summary format
CHECK 21b: PASS -- gc on empty / non-archived registries
CHECK 22: PASS -- live-dot pytest coverage
CHECK 23: PASS -- --json schema stable across --all/--stale; banner omitted
CHECK 24: PASS -- legacy records tolerated
CHECK 25: PASS -- cst set --last-user-prompt rejected
CHECK 26: PASS -- tool_use with no name tolerated
CHECK 28: PASS -- unicode/emoji/CJK/RTL round-trip
CHECK 29: PASS -- fresher-wins for last_user_prompt
CHECK 30: PASS -- config rejects 0/negative/bool; names key in log
CHECK 27: PASS -- Sprint 1 regression (all prior pytest modules green)

=== SUMMARY: 32 pass, 0 fail ===
```

### `git log --oneline`

```
2474b35 Sprint 2: update SKILL.md for v0.2.0 (progress capture, new subcommands, hook + display contracts)
2449659 Sprint 2: installer wires statusLine with full canonical shape; preserves existing
734fc74 Sprint 2: focus/resume/gc/review-stale/statusline modules and tests
f575f67 Sprint 2: cst CLI — list multi-line/compact/stale, resolver prefix matching, focus/resume/gc/review-stale/statusline subcommands
86b3f91 Sprint 2: livedot module (ps -o pid,tty,comm parsing, silent degrade)
e9d6085 Sprint 2: hook activity writes last_user_prompt from stdin; creates skeleton on unknown session_id
7ab567c Sprint 2: scanner progress extraction (50-line tail, tool hints, truncation, fresher-wins)
ed6002f Sprint 2: config loader (stale_threshold_seconds with env/file/default precedence, strict validation)
1f53ec3 Sprint 2: registry — add progress fields (scanner-owned), update() refuses them, read() backfills
3dc948b report: document post-Sprint 1 polish fixes (status enum + bin file guard)
fe2f83b install.sh: refuse to overwrite regular file at ~/.local/bin/cst
a8b1e17 cst set: reject invalid --status values (enum: in_progress|blocked|waiting|done)
de53c21 Sprint 1 handoff: generator_report.md
e97516a Sprint 1 tests (36 passing) + SKILL.md
cd037de Sprint 1 core: registry, scanner, hooks, cst CLI, installer
c0936b3 Sprint 1 kickoff: spec + brainstorm + sprint_contract in place
```

## Non-blocking nits applied per §12

- Focus byte-match tests parallel to resume ones:
  `test_focus_iterm_osascript_string_matches_template` and
  `test_focus_terminal_app_osascript_string_matches_template` in
  `tests/test_focus.py`.
- Check 29 mtime delta uses ±3600s (CI-robust), not ±1s.
- Config validation warning names the offending key
  (`stale_threshold_seconds`) in the log line; tested by
  `test_config_zero_or_negative_falls_back_to_default` and
  `test_config_bad_value_type_falls_back_to_default_and_logs`.

---

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
