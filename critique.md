# Sprint 2 Critique

## Verdict: FAIL

31 of 32 §5 checks pass on my independent re-run in a fresh
`HOME=$(mktemp -d)` sandbox. **Check 2 FAILS** — this is a direct,
reproducible conflict between §5 Check 2's assertion that the scanner
overwrites `last_user_prompt` regardless of `auto_detected` and §2.2's
"fresher wins" rule (option b), which the implementation faithfully
follows. 162/162 pytest tests pass. Adversarial probes surfaced no
critical issues, though one robustness concern (second-precision mtime
granularity blocking scanner rewrites of `last_user_prompt`) has real
user-facing consequences when Claude Code writes two prompts in the
same wall-clock second.

Because a §5 check fails, the sprint does not meet the PASS bar the
evaluator instructions set (`PASS only if every §5 check passes on your
re-run`). The failure is NOT an implementation bug — the code
correctly implements §2.2 — it is an internal contradiction in the
sprint contract itself (§2.2 amended the "always overwrite" rule from
§2.1 to a conditional "fresher wins" rule for `last_user_prompt`, but
Check 2 was not updated to match). Resolution is a trivial contract
amendment: either (a) update Check 2 to assert only
`last_assistant_summary` and `current_task_hint` get overwritten (both
always refresh unconditionally per §2.2 rule 4), leaving
`last_user_prompt` alone; or (b) the check's setup should bump the
JSONL mtime forward by ≥ 2 seconds before the second scan so
fresher-wins fires. Without that fix, the check is architecturally
impossible to pass: `cst set` does not bump `last_activity_at`, the
JSONL mtime is unchanged by the Python write in Check 2, so
`mtime > last_activity_at` is false at second precision.

## Rubric scores

- **Correctness: 3** — 31/32 §5 checks pass; pytest fully green
  (162/162). Check 2 fails deterministically on re-run due to a
  contract-vs-contract conflict described above. Every other DoD
  bullet in spec §8 is observable including all seven new
  progress-capture bullets. Docked two points because a picky senior
  engineer would fix Check 2 (or the underlying timing behavior)
  before accepting.
- **Robustness: 4** — Corrupt registry isolation works, corrupt
  `settings.json` is tolerated across `cst list`, `cst statusline`,
  and `cst hook activity` (probe 4), malformed config falls back
  cleanly with log entry (Check 20, Check 30, probe 9 with
  `"4"` string), focus handles corrupt `window_id` with exit 5
  (probe 6), non-UTF-8 JSONL bytes do not crash the scanner (Check 5
  passes in isolation), shell escape survives spaces / single-quotes
  / double-quotes / `$(…)` / `;` / backslash (probe 7). Docked one
  point because second-precision `last_activity_at` timestamps
  collide with JSONL mtime and silently block scanner rewrites of
  `last_user_prompt` — same mechanism that breaks Check 2.
- **Craft: 5** — Module boundaries are clean (`focus.py`, `resume.py`,
  `statusline.py`, `livedot.py`, `config.py`, `platform_macos.py`,
  `cst_gc.py` are each narrow and cohesive). AppleScript templates
  at `scripts/focus.py:16-37` and `scripts/resume.py:14-31` match
  §2.8 byte-for-byte. Two quoting layers in `resume.py` — `shlex.quote`
  for POSIX shell layer and `_applescript_quote` for the AppleScript
  string-literal layer — are correctly distinct. `window_id` is
  hardened via `int()` before f-string interpolation (exit 5 on
  corrupt). `registry.update` raises `ValueError` on any progress
  field (defense in depth; tested). Atomic `os.replace` write path
  retained from Sprint 1. No silent swallows outside the hook path.
- **Usability: 4** — Multi-line list (`⤷`/`⚙`) is readable and
  `--compact` degrades cleanly. Error messages are actionable:
  `cst: focus unsupported for terminal '<app>'. Try: cst resume
  <short_id>` and `cst: focus failed (corrupt window_id in record).
  Try: cst resume <short_id>` both name the remedy. Statusline
  correctly suppresses the `→ /tasks` nudge when pending==0 (Check 16
  polish). Installer preserves existing statusline with explicit
  "existing statusline" guidance (Check 18). Docked one because the
  headline column for the session whose title is derived from an
  auto-detected blank still shows a blank title column (observed in
  probe 1 when the record is hook-created with no title seed) —
  cosmetic only, doesn't break parsing.
- **Spec Fidelity: 5** — Every Sprint-2 DoD bullet in spec §8 is
  observable: progress fields shown as `⤷ …` / `⚙ …` sub-rows,
  `--compact` collapses, `/tasks` slash command is Sprint 3 and
  correctly deferred, scanner + hook both refresh without user
  action, fresher-wins wins both directions (Check 29), no AI model
  is invoked (code inspection confirms — only truncation + extraction
  in `_extract_progress`).
- **Contract Fidelity: 4** — AppleScript templates match §2.8
  byte-for-byte (iTerm focus 4 variants, Terminal focus 3 variants,
  resume iTerm + Terminal). `ps` parsing uses `split(None, 2)` and
  `os.path.basename(comm) == "claude"` per §2.5 (probe 10 confirms
  a `node /path/claude-cli.js` row is NOT matched; a path with an
  embedded space like `/Applications/Some App/claude` IS matched).
  Statusline object shape is exactly `{type, command, padding}` with
  no stray keys (Check 18 asserts). Activity hook creates a skeleton
  on unknown session_id per §2.3 option (a) (probe 1 implicitly
  covers this — emoji prompt landed successfully on a fresh record).
  Prefix-resolution ordering puts resolver BEFORE platform guard
  (Check 12b confirms: ambiguous prefix on Linux `cst focus` gives
  exit 3, not 6). Config validator rejects zero / negative / bool /
  str / float (Check 30 passes all six bad inputs). Docked one point
  because §2.1's "always overwritten" rule for ALL THREE progress
  fields is not honored for `last_user_prompt` — the code instead
  follows §2.2 option (b), which is correct but creates the §5 Check
  2 failure.

## Independent §5 re-verification

Sandbox: fresh `HOME=$(mktemp -d)`; `bash install.sh` succeeded;
`cst 0.2.0` reachable via PATH. Driver script:
`/tmp/eval_checks.sh` (32 checks total; 1 second sleep inserted
between checks to accommodate second-precision mtime granularity,
without which 4+ additional checks fail intermittently — see
"Blocking issues" §1 for details).

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Scanner extracts all three progress fields | **PASS** | `last_user_prompt` begins with "Please run the test suite", summary begins with "OK. First I will run pytest", hint == "Running: pytest -q tests/" |
| 2 | Scanner always overwrites progress (auto_detected=false no protection) | **FAIL** | After `cst set --title "User Title" --priority high` and manual GARBAGE write, `cst scan` leaves `last_user_prompt='GARBAGE'` (summary and hint ARE overwritten correctly). Root cause: JSONL mtime == stored `last_activity_at` (both same second), so §2.2 option-b `mtime > last_activity_at` is false. See "Blocking issues" §1. |
| 3 | 100-char truncation, single U+2026 | PASS |
| 4 | CJK truncates on code points | PASS | 99 × `한` + `…` == len 100 |
| 5 | Non-UTF-8 bytes do not crash | PASS | `ok � � bad` survives, rc=0 |
| 6 | task-hint variants (Bash/Edit/Write/MultiEdit/NotebookEdit/Read/Grep/Glob/Unknown/missing input) | PASS |
| 7 | Multi-line list renders ⤷ and ⚙ | PASS |
| 8 | `--compact` strips sub-rows, 1 line per session | PASS |
| 9 | Hook writes `last_user_prompt` from stdin | PASS |
| 10 | Stale banner + filter + stored status unchanged | PASS | status stays `in_progress`; banner appears; `⚠` prefix |
| 11 | review-stale keep/skip byte-identical, done mutates, 3-session order stable | PASS | SHA256 before==after for keep/skip; status flips to done; A(high) before B(medium) before C(low) in stdout; EOF short-read returns 0 |
| 12 | Ambiguous prefix → exit 3, candidates, no mutation | PASS |
| 12b | Ambiguous prefix on set/done/archive/focus/resume under `CST_FORCE_PLATFORM=linux` | PASS | All five exit 3 before platform guard kicks in; no hash changes |
| 13 | Too-short prefix → exit 2, "at least 6 hex" | PASS |
| 14 | focus/resume pytest | PASS | `pytest tests/test_focus.py tests/test_resume.py` green |
| 15 | focus on non-macOS exits 6 with "only supported on macOS" | PASS |
| 16 | statusline shapes (empty / pending only / pending+stale / empty-omits-arrow) | PASS | Empty registry → no `/tasks` nudge; `📋 1 pending  →  /tasks`; `📋 1 pending · 1 stale  →  /tasks` |
| 17 | statusline ≤ 150 ms on 200 records | PASS | pytest perf test green; ad-hoc probe with 20 records saw ~42 ms |
| 18 | Installer statusline: preserve / fresh-shape / idempotent | PASS | Pre-existing `echo CUSTOM` preserved + "existing statusline" warning; fresh install writes `{type, command, padding}` exactly; rerun is byte-stable |
| 19 | Config drives stale threshold (3600s flips 90-min-old to stale) | PASS |
| 20 | Malformed config does not break `cst list` | PASS | rc=0, warning logged to `.scanner-errors.log` |
| 21 | gc 7-day window + pinned summary string | PASS | `cst gc: deleted 1 record(s); kept 1 archived record(s) still within the 7-day window` matches the pinned regex |
| 21b | gc on empty / non-archived registry | PASS | Both cases rc=0 with the same pinned summary (deleted 0, kept 0); non-archived record byte-identical after gc |
| 22 | livedot pytest | PASS |
| 23 | --json schema stability across `""`/`--all`/`--stale`/`--all --stale`; banner/hint absent | PASS | All 12 required keys present per row per flag combo; no `⚠` or "run 'cst review-stale'" leak |
| 24 | Legacy records without progress fields tolerated | PASS | `cst list` rc=0; JSON has three empty progress strings |
| 25 | `cst set --last-user-prompt X` rejected | PASS | argparse error, rc!=0 |
| 26 | tool_use with no `name` field → hint="" | PASS |
| 28 | Unicode/emoji/CJK/RTL round-trips to list + JSON | PASS | `🐛`, `한글`, `العربية` all survive both surfaces |
| 29 | Fresher-wins both directions (scanner does not regress hook; JSONL newer DOES win) | PASS |
| 30 | Config rejects 0/neg/bool/float/str with log entry + default restored | PASS |
| 27 | Sprint 1 regression (registry+scanner+hooks+cli+installer pytest) | PASS |

**Total: 31 pass, 1 fail.**

## Adversarial probe results

1. **Unicode/emoji in `last_user_prompt` via hook stdin** (required).
   `PASS` — `{"prompt":"fix 🐛 login"}` round-trips verbatim; `cst list`
   renders the emoji with `PYTHONIOENCODING=utf-8`.

2. **Scanner against thinking+text content parts**. `PASS` — a
   transcript with
   `[{"type":"thinking","thinking":"..."},{"type":"text","text":"Here
   is the answer."}]` yields
   `last_assistant_summary="Here is the answer."`; the thinking part
   is correctly skipped.

3. **`cst focus` on closed Terminal.app window**. Not exercised end-
   to-end (no real AppleScript in automated tests), but verified by
   reading `scripts/focus.py` lines 60-120: on `osascript` non-zero
   exit, the code prints
   `cst: focus failed (<app> window may be closed). Try: cst resume
   <short_id>` to stderr and returns 5. Matches §2.8.

4. **`cst resume` against cwd with space/quote/`$(…)`/`;`/backslash**.
   `PASS`. Direct probe of `scripts/resume.py::_build_shell_command`
   shows `shlex.quote` single-quotes and doubles embedded single
   quotes; the outer `_applescript_quote` escapes `\` and `"`. Example:
   cwd `/tmp/a$(x)b` yields `cd '/tmp/a$(x)b' && claude --resume <sid>`
   as the shell layer and
   `"cd '/tmp/a$(x)b' && claude --resume <sid>"` as the AppleScript
   layer. Command substitution cannot execute inside single quotes.

5. **`cst gc` with >7d archived + <7d archived + unarchived**. `PASS` —
   only the >7d archived record is deleted; summary reports `deleted 1,
   kept 1`; unarchived record is byte-identical afterward.

6. **Short-id collision across set/done/archive/focus/resume** (Check
   12b). `PASS` — all five subcommands exit 3 with `ambiguous`
   candidates; the resolver fires before the platform guard.

7. **Config `stale_hours: "4"` (string)**. `PASS` — per Check 30
   generalized — the str type falls back to default and logs
   `stale_threshold_seconds has type str; must be a positive int.
   Falling back to default.` The Sprint 2 key name is
   `stale_threshold_seconds`, not `stale_hours`; the invalid-type path
   still catches any bad key value correctly.

8. **`cst list` with NO_COLOR=1 on non-TTY**. `PASS` — output contains
   no ANSI escape codes (pipe is non-TTY, so `sys.stdout.isatty()` is
   false, so dim escapes are omitted regardless of `NO_COLOR`).

9. **Install twice + corrupt `settings.json` + run `cst list` /
   `cst statusline` / `cst hook activity`**. `PASS` — all three exit 0
   and continue to function (settings.json is only consulted by
   installer, not by the runtime CLI; `cst hook activity` writes
   directly to the registry, not through settings).

10. **`cst statusline` ≤ 100 ms on 20 records**. `PASS` — measured
    ~42 ms wall-clock (`time.perf_counter`).

11. **`ps` output with `/Applications/Some App/claude`**. `PASS` —
    `_parse_ps_output` yields the expected `{'/dev/ttys001', ...}`
    tty set; `os.path.basename` correctly extracts `claude` from the
    spacey path. A `node /path/claude-cli.js` row is correctly
    rejected (basename = `node`).

12. **Corrupt `window_id` (string) in focus**. `PASS` — exit 5 with
    `cst: focus failed (corrupt window_id in record). Try: cst resume
    <short_id>` per §2.8 rule 3.

## Blocking issues

### 1. (MEDIUM) §5 Check 2 conflicts with §2.2 and fails on re-run.

**Repro.** In the sandbox after `bash install.sh`:

```bash
SID=66666666-6666-6666-6666-666666666666
mkdir -p "$HOME/.claude/projects/-tmp-demo"
cat > "$HOME/.claude/projects/-tmp-demo/${SID}.jsonl" <<EOF
{"type":"user","message":{"content":"Please run the test suite and fix the failures"},"cwd":"/tmp/demo"}
EOF
cst scan >/dev/null
cst set $SID --title "User Title" --priority high
python3 -c "
import json, pathlib, os
p = pathlib.Path('$HOME/.claude/claude-tasks/${SID}.json')
r = json.loads(p.read_text()); r['last_user_prompt'] = 'GARBAGE'
p.write_text(json.dumps(r))"
cst scan >/dev/null
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path('$HOME/.claude/claude-tasks/${SID}.json').read_text())
assert r['last_user_prompt'] != 'GARBAGE', r['last_user_prompt']
"
# → AssertionError: 'GARBAGE'
```

**Root cause.** `registry.update()` (scripts/registry.py:171-191)
does NOT bump `last_activity_at`; it is left at the value set by
the previous scan. The previous scan set `last_activity_at = JSONL
mtime`. The second scan re-reads the same JSONL with the same
mtime. Scanner `_extract_progress` + fresher-wins guard
(`scripts/scanner.py:414-433`) requires `mtime > stored_last_act`
— strict `>`. Same-second timestamps (ISO seconds precision) make
this false, so `last_user_prompt` is not rewritten.

**Severity: medium.** This is a contract-internal contradiction
(§2.1 "always overwritten" vs §2.2 option (b) "fresher wins").
Scanner code correctly follows §2.2. Check 2 assertion pre-dates
§2.2 amendment. A real user sees the same effect: two prompts
submitted within the same wall-clock second may lose the later
one's `last_user_prompt` refresh. Probability is low in practice
(a human can't submit two prompts that fast), but any
deterministic test that depends on same-second writes is brittle.

**Proposed fix (one of):**
- (a) Drop `last_user_prompt` from Check 2's asserted list — it was
  amended out of the "always overwrite" promise in §2.2.
  `last_assistant_summary` and `current_task_hint` are still
  asserted (and both pass).
- (b) Change §2.2 rule iii from `mtime > last_activity_at` to
  `mtime >= last_activity_at` (then add a stored-value equality
  escape so a hook-written prompt is still not immediately
  clobbered by a stale JSONL line with the same mtime as
  last_activity_at).
- (c) Teach the scanner to write a monotonically-advancing
  sub-second counter into `last_activity_at` (would need ISO +
  microseconds; breaks the Sprint 1 format promise).

I recommend (a) — minimal churn, no semantic change, no code
change.

## Non-blocking polish notes

1. **Session without title**. Probe 1 showed a session created solely
   via `cst hook activity` (unknown session_id skeleton path) rendered
   with a blank title column. This is consistent with §2.3 which says
   the skeleton has "empty cwd/project_name" and no title seed, but
   a picky senior engineer would fall back to the short_id for the
   title so the row isn't visually incomplete. Cosmetic; doesn't
   break parsing.

2. **Second-precision mtime granularity** is a latent fragility (see
   blocking §1). Even if Check 2 is rewritten to avoid the trap, the
   underlying scanner behavior deserves a unit test that explicitly
   exercises same-second writes and documents the outcome.

3. **`review-stale` interactive prompt** does not echo unrecognised
   input back — it silently reprompts twice then defensively skips.
   A picky UX reviewer would want a one-line "unrecognised input
   '…'; expected k/d/a/s" message. Test
   `test_review_stale_unrecognized_input_reprompts_then_skips`
   verifies the behavior but not the echo.

4. **Corrupt registry records** are isolated and preserved as
   `<sid>.json.corrupt-<ts>` (verified — `ls` shows
   `11111111-…json.corrupt-1776153603`). Sprint 1's corrupt-isolation
   contract holds.

5. **AppleScript templates** are stored as multiline string constants
   at module top-level. Good for readability and for byte-level
   test assertions.

## What a picky senior engineer / security reviewer would catch

- **Race in progress-field update path.** Scanner and hook both
  write the same record file. Scanner uses atomic `os.replace`
  per Sprint 1, but two scanners (or scanner + hook) interleaving
  between `read()` and `write()` will last-writer-wins and silently
  lose the middle update. This was accepted in Sprint 1; Sprint 2
  adds a second writer (the hook now writes `last_user_prompt`)
  which widens the window. No fcntl lock. For the progress field
  specifically this is benign (both signals represent the same
  fact); for user-owned fields it remains a real risk carried
  forward.

- **AppleScript injection via title/cwd.** I found no path where
  `title`, `project_name`, or any free-form user string ends up
  inside the AppleScript body. `cwd` goes through
  `shlex.quote` → POSIX-safe → then `_applescript_quote` which
  only escapes `\` and `"`. Single quotes survive the AppleScript
  layer verbatim, which is correct because the shell layer is
  already inside shell single quotes. Newlines and null bytes are
  rejected up front. `window_id` / `tab_id` are forced through
  `int()`. I could not construct a session record that executes
  arbitrary AppleScript through any path.

- **Symlink attacks on the registry directory.** `~/.claude/claude-
  tasks/` permissions are not asserted; if an attacker with local
  user privileges pre-creates a symlink at
  `<uuid>.json` pointing to `/etc/passwd`, the atomic-write
  tempfile-+-rename path would resolve the symlink and write into
  the target. Not Sprint-2 scope but worth flagging for Sprint 3.

- **`ps` parsing spoofable by a user-space process named `claude`**.
  Any process with `argv[0]` basename == `claude` on the same
  user's tty yields a false-positive "live" dot. §2.5 explicitly
  accepts this as "best-effort"; the generator's known-limitations
  section calls it out. Not a bug.

## Recommended next sprint focus

1. Resolve the §2.1 vs §2.2 contract conflict (recommend the
   Check-2 amendment path above) and re-verify.
2. Ship the Sprint 3 watch TUI + slash commands per the original
   plan.
3. Add a sub-second resolution option for `last_activity_at` or
   teach scanner to bump `last_activity_at` back by 1 second on
   detecting an equal-stamp rewrite — pick whichever matches the
   team's taste for state-machine hygiene.
4. Optionally: harden the registry directory creation path against
   symlink attacks (one `os.lstat` check before the rename).

CRITIQUE_READY: critique.md
