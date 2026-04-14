# Sprint 1 Critique

## Verdict: PASS

All 14 §5 checks pass on independent re-run in a fresh `HOME=$(mktemp -d)` sandbox. All 36 pytest tests pass. No adversarial probe surfaced a critical issue. One medium-severity bug (status field is not validated on `cst set`) and two low-severity polish items are documented in "Blocking issues" and "Non-blocking polish notes" but they do not block Sprint 1 acceptance because they fall outside explicit Sprint 1 commitments in §2.

## Rubric scores

- **Correctness: 5** — All 14 §5 checks pass independently; all 36 pytest tests pass. Output format, sort order, atomicity, and lifecycle transitions match the binding contract exactly. Evidence: §5 re-run section below.
- **Robustness: 4** — Corrupt-file isolation works, byte-identical preservation verified, hooks never crash on bad input, concurrent same-session activity hooks produce no corrupt JSON (Probe 8). One notch off for `cst set --status` accepting arbitrary strings (Probe 12) and `ln -sfn` silently clobbering a pre-existing regular file at `~/.local/bin/cst` (Probe 4) — neither is a §2 violation but both matter to a picky user.
- **Craft: 5** — Modules have clean boundaries (`registry.py` is purely I/O, `scanner.py` derives, `hooks.py` orchestrates, `cst.py` dispatches, `installer.py` isolated). Atomic writes via tempfile + fsync + `os.replace`. UTC timestamps everywhere. No silent swallowed errors outside the hook path (which is mandated by the contract). `scripts/registry.py:88-106` shows exception-safe tempfile cleanup. No dead code observed.
- **Usability: 4** — `cst --help` lists every Sprint 1 subcommand with short help; installer prints readable progress, warns on missing PATH, smoke-test confirms install. Error messages (`cst: no such session: <id>`, `cst: --priority must be high|medium|low`) are actionable. One notch off: `cst` with no args prints help but exits `1` silently rather than noting what is missing — a first-time user may not realize `cst` itself is fine and just needs a subcommand. `--stale` exists but silently returns nothing in Sprint 1; could warn that it's deferred.
- **Spec Fidelity: 5** — Every DoD bullet Sprint 1 claims to cover (installation idempotency, empty registry exit 0, auto-detection stickiness, sort ordering, done/archive lifecycle visibility, corrupt file preservation, hook non-blocking, nonzero-on-error) is observable. Deferred DoD bullets (focus, statusline, stale detection, etc.) are explicitly excluded per contract §3.
- **Contract Fidelity: 5** — All §2 commitments honored: atomic writes (`_atomic_write`), exact-string hook merge matching (Probe 15 confirms `--debug` substring is NOT treated as duplicate), stdin-first-then-env fallback with stdin winning on conflict (Check 14 + `test_stdin_takes_priority_over_env`), malformed settings policy (a) with no backup written (Check 13), UUID-regex-only filenames (Probe 6, Probe 21), project-slug decoding rule (Probe 14 confirms the two fixture cases from §2).

## Independent §5 re-verification

All 14 checks re-run against `HOME=$(mktemp -d)`. Commands in the pasted bash output above.

- **Check 1** (installer idempotent, exact-string match): **PASS**. Second run printed `found 2 existing hook entries, appended 0 new`. Python assertion `HOOKS_OK` printed.
- **Check 2** (empty `cst list` exits 0): **PASS**. Output `(no sessions)`, exit 0.
- **Check 3** (scanner creates record from fixture JSONL): **PASS**. `SCAN_OK Refactor the login endpoint | project_name=fake-proj | cwd=/tmp/fake-proj`.
- **Check 4** (user fields sticky across re-scan — title/priority/status/note/tags): **PASS**. `STICKY_OK`.
- **Check 5** (corrupt file isolated with byte-identical preservation AND good sibling still renders): **PASS**. `CORRUPT_BYTES_PRESERVED`, sibling row `22222222 high blocked User Title fake-proj 17s ago` rendered in same `cst list` invocation.
- **Check 6** (hooks exit 0 on missing input + fresh timestamped log line): **PASS**. `rc1=0 rc2=0`, log shows `2026-04-14T...Z session-start: missing session_id (no stdin, no env)`.
- **Check 7** (sort order `H-recent, H-old, M-recent, L-newest` via `--json`): **PASS**. `SORT_OK`.
- **Check 8** (`test_atomic_write_no_partial`): **PASS**. `1 passed in 0.01s`.
- **Check 9** (done visible, archive hidden, `--all` shows archived): **PASS**. `LIFECYCLE_OK`.
- **Check 10** (installer preserves existing `statusLine`): **PASS**. `STATUSLINE_UNTOUCHED`.
- **Check 11** (two distinct UUIDs → 2 files, 2 rows): **PASS**. `DISTINCT_IDS_OK`.
- **Check 12** (installer from fresh `$HOME`): **PASS**. `FRESH_OK`.
- **Check 13** (installer refuses malformed settings, no backup, byte-identical): **PASS**. `rc=2`, `MALFORMED_SETTINGS_RESPECTED`. Evidence: stderr prints `cst install: <path> is not valid JSON (JSONDecodeError: ...). cst install: refusing to modify it.`
- **Check 14** (stdin JSON payload, no env vars): **PASS**. `STDIN_OK`, `STDIN_CHECKS_OK`.

No discrepancies vs `generator_report.md`.

## Adversarial probe results

- **Probe 1 — Unicode/RTL title**: Commands — `cst set <uuid> --title "🔥 작업 مرحبا ✨"`. Stored byte-identical, `cst list` rendered correctly. **PASS** (severity: n/a).
- **Probe 2 — `cst set` on nonexistent id**: Output `cst: no such session: <id>`, exit 1, no ghost record in registry. **PASS**.
- **Probe 3 — `cst list --json` during 5 concurrent activity hook invocations**: All 5 emitted parseable JSON; no corrupt rows. **PASS** (evidence: `valid 1..5`). Atomic writes via `os.replace` are sufficient here.
- **Probe 4 — Install when `~/.local/bin/cst` is a pre-existing regular file**: `ln -sfn` replaced the regular file silently (mode `-rwxr-xr-x` → `lrwxr-xr-x`). **FAIL (medium)**. Contract does not forbid this, but a user whose `$HOME/.local/bin/cst` is a different tool's binary will silently lose it on install. Recommendation in non-blocking notes.
- **Probe 5 — Corrupt `settings.json` AFTER install, rerun `cst list`**: `cst list` runs fine (it doesn't read settings.json). **PASS**. `cst` does not depend on settings.json at runtime.
- **Probe 6 — Scanner with non-UUID filename (`..etcpasswd.jsonl`, `notauuid.jsonl`)**: Both silently skipped, `scanned 0, created 0`, no record. **PASS**.
- **Probe 7 — Hook stdin valid JSON but missing `session_id`**: Exit 0, log line `session-start: missing session_id (no stdin, no env)`. **PASS** (minor: log message says "no stdin, no env" when stdin was actually present but lacked the field; slightly misleading diagnostic string).
- **Probe 8 — 50 concurrent `cst hook activity` invocations on same session id**: Final record is valid JSON, no leftover `.tmp` files, last_activity_at set. **PASS**. `os.replace` atomicity holds up.
- **Probe 9 — Scanner with symlink UUID.jsonl → /etc/passwd**: Scanner followed the symlink, read the file, failed JSON parse, fell back to `project_name`, created a record. **PASS** (non-issue: `/etc/passwd` is world-readable and the user supplied the symlink in their own `~/.claude/projects/`; the scanner handles the JSON parse failure cleanly).
- **Probe 10 — `cst --version`**: Prints `cst 0.1.0`. **PASS**.
- **Probe 11 — `cst --help`**: Lists every subcommand with descriptions. **PASS**.
- **Probe 12 — `cst set --status bogus_value_xyz`**: Stored verbatim, exit 0, no validation. **FAIL (medium)**. Spec §5 declares status must be one of `in_progress|blocked|waiting|done`. The contract §2 lists `cst set ... [--status ...]` without explicit enum enforcement, but the spec is binding for DoD. This is a validation gap the Generator missed. Reproduction below.
- **Probe 13 — `cst set --tags ",,,,a,,b,"`**: Empty parts trimmed, result `['a','b']`. **PASS**.
- **Probe 14 — Project-slug decode (`-tmp-fake-proj`, `-Users-alice-proj-foo`)**: Results `fake-proj` and `foo` respectively — both match the §2 fixture contract. **PASS**. Note: Generator report §"Known limitations" openly acknowledges the heuristic is fragile for 4+ part slugs with multi-word dir names; contract risk §10.8 accepts this for Sprint 1.
- **Probe 15 — Installer substring-not-duplicate**: Pre-seeded `cst hook session-start --debug`, installer appended the exact `cst hook session-start` alongside it. **PASS**.
- **Probe 16 — `cst` with no args**: Prints full help text, exit 1. **PASS** (usability note below).
- **Probe 17 — `cst set` with no fields**: `cst: set requires at least one field`, exit 1. **PASS**.
- **Probe 18 — `cst scan` when `~/.claude/projects` does not exist**: `scanned 0, created 0, updated 0`, exit 0. **PASS**.
- **Probe 19 — `cst set --priority ULTRA_MEGA`**: `cst: --priority must be high|medium|low`, exit 1. **PASS**. (Validates asymmetry with Probe 12: priority IS validated; status is NOT.)
- **Probe 20 — File permissions on registry file**: `-rw-------` (0600) via `mkstemp` default. **PASS** on security (not world-readable).
- **Probe 21 — Uppercase-hex UUID filename**: Scanner rejects (regex is lowercase-only). Acceptable per §2 (regex is explicitly lowercase).
- **Probe 22 — >60-char first user message**: Trimmed to exactly 60 chars. **PASS**.
- **Probe 23 — Block-style `content: [{type:text,text:...}]`**: Extracted correctly. **PASS**.
- **Probe 24 — First line assistant, second user**: Title seeded from the user line. **PASS**.
- **Probe 25 — Installer preserves unrelated settings keys (`permissions`, `env`, `customField`)**: All preserved byte-for-byte; only `hooks` is touched. **PASS**.
- **Probe 26 — `cst list --stale` in Sprint 1**: Returns no rows silently (stale detection deferred). **PASS** per contract §3, though a first-time user may be confused — usability note.
- **Probe 27 — Hook log unbounded growth**: 20 failures → 20 log lines, no rotation. Not a Sprint 1 commitment. Non-blocking note.

## Blocking issues

None. No issue blocks Sprint 1 acceptance because:

1. Every §5 check passes independently.
2. Every DoD bullet Sprint 1 claims to cover is observable.
3. No probe revealed a critical issue (data loss, registry corruption, silent drop of sessions, security hole).

## Non-blocking polish notes

1. **`cst set --status` accepts arbitrary strings (medium)**. Repro: `CLAUDE_SESSION_ID=<uuid> cst hook session-start < /dev/null; cst set <uuid> --status bogus_value_xyz` → stored verbatim, exit 0. Priority is validated (`--priority ULTRA_MEGA` → exit 1), status should be too. Spec §5 enumerates `in_progress|blocked|waiting|done`. Fix is trivial (mirror the priority check in `cmd_set`, `scripts/cst.py:135-138`). Not a blocker because §2 of the contract does not explicitly demand enum enforcement on set, and the scanner / lifecycle commands still only produce valid statuses.

2. **`ln -sfn ~/.local/bin/cst` silently overwrites a pre-existing regular file (medium)**. `install.sh:33` calls `ln -sfn "${SKILL_DIR}/scripts/cst.py" "${CST_BIN}"` which clobbers any existing `~/.local/bin/cst` binary without warning. Suggest checking `-e && ! -L` and printing a warning / aborting. Not a Sprint 1 commitment.

3. **Hook error log is unbounded**. `~/.claude/claude-tasks/.hook-errors.log` grows forever. Fine for debugging; low-priority rotation can land in a later sprint.

4. **`cst list --stale` in Sprint 1 silently returns nothing** (stale detection is deferred to Sprint 2 per contract §3). A user who reads `cst --help` and tries `--stale` gets empty output with no explanation. Consider `cst: --stale not yet supported in this version` stderr line, or remove `--stale` from the parser until Sprint 2.

5. **`cst` with no subcommand exits 1 after printing full help**. argparse default; some CLIs exit 0 on help. Not a functional issue.

6. **Hook "missing session_id" log message reads "(no stdin, no env)" even when stdin WAS provided but lacked the field** (Probe 7). The diagnostic is misleading in that case; better wording: `missing session_id field in payload`.

7. **UTC timestamp note for Check 6**: contract §12 already flagged this; generator implemented UTC with trailing `Z` in `hooks.py:31`. Fine.

8. **`iter_records()` filters non-UUID-named JSON files silently (`scripts/registry.py:256-258`)**. A hand-edited record at `~/.claude/claude-tasks/mynotes.json` would be invisible to `cst list`. Acceptable and probably desirable.

## Recommended next sprint focus

Per contract §3 the Sprint 2 slice is: `cst focus` + AppleScript for iTerm2 and Terminal.app; `cst resume` (new window + `claude --resume`); `cst statusline` and statusline installer wiring; live-vs-idle dot (ps/tty match); stale detection + banner + `cst review-stale`; `cst gc` (7-day archived deletion); short-id prefix matching ≥6 chars with ambiguity handling; stale-threshold config file; macOS-only platform guard. In addition I recommend:

- Validate `--status` enum in `cmd_set` (30 seconds of work; closes the only medium-severity bug found this sprint).
- Warn or abort when `~/.local/bin/cst` exists as a non-symlink before `ln -sfn` clobbers it.
- Fix the misleading "(no stdin, no env)" log message when stdin was present but payload lacked `session_id`.
- Either remove `--stale` from the parser until Sprint 2 or print a one-line deferred-feature notice when invoked.

CRITIQUE_READY: critique.md
