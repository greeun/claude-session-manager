# Sprint 2 Contract — Claude Session Manager

> Sprint 1 is shipped (43 passing tests, 14/14 observable checks). Its
> contract is preserved at `sprint_1_contract.md` and in git history.
> This file supersedes it for Sprint 2.

## 1. Sprint 2 goal

Turn the registry into a useful daily driver for a macOS Claude Code
power user: the scanner extracts at-a-glance progress fields from
transcripts, `cst list` renders multi-line rows (with a `--compact`
fallback), stale detection + triage surface forgotten work,
`cst focus` / `cst resume` / statusline wire the tool into actual
workflows, `cst gc` reclaims disk, short-id prefix matching lands for
ergonomics, and a config file + macOS guard + `.claude/settings.json`
statusline wiring round out the installation story.

## 2. Features in this sprint

The slice below targets specific Definition of Done bullets from
`spec.md` §8 (including the new **Progress capture and display**
subsection).

### 2.1 Record schema extension (new progress fields)

Three new **scanner-owned** fields are added to every record. Binding
rules:

- `last_user_prompt` — string (default `""`). Truncated to at most
  100 characters; longer content ends with `"…"` (single U+2026 code
  point, not three dots). Single line: the first newline terminates
  extraction.
- `last_assistant_summary` — string (default `""`). Same 100-char /
  1-line truncation rule.
- `current_task_hint` — string (default `""`). Built from the last
  `tool_use` block near the transcript tail. Format is one of:
  - `"Running: <command>"` when the tool is `Bash` and the
    `command` input field is a non-empty string.
  - `"Editing: <relpath-or-basename>"` when the tool is `Edit`,
    `Write`, `MultiEdit`, or `NotebookEdit` and `file_path` is a
    non-empty string. Relpath is computed as `os.path.relpath(file_path, cwd)`
    when `cwd` is set and `file_path` starts with `cwd`; otherwise
    basename.
  - `"Reading: <relpath-or-basename>"` for `Read`.
  - `"Searching: <pattern>"` for `Grep`/`Glob` when `pattern` is a
    non-empty string.
  - `"<ToolName>"` (just the bare tool name) as a fallback when the
    tool is recognised but its distinguishing input field is
    missing/empty.
  - `""` (empty) when the last 50 JSONL lines contain no `tool_use`
    block, when the tool name is missing, or when parsing fails.
  The composed hint is also truncated to 100 chars with `"…"` suffix.

**Ownership rule (binding).** These three fields are scanner-owned
and **always overwritten** on every scan pass. `auto_detected=false`
does NOT protect them. The scanner never reads them, only writes
them. A user-set value (from `cst set` or any CLI path) is explicitly
rejected — there is no CLI flag to write them. This is the inversion
of the user-owned fields (`title`/`priority`/`status`/`note`/`tags`).

`new_record()` seeds the three fields to `""`; existing Sprint 1
records on disk get the fields added (with `""`) on first write after
upgrade — reads tolerate their absence and treat missing as `""`.

Satisfies DoD bullets: *"Progress fields are never written by the user
and are always refreshed by the scanner / hook without user action"*,
*"No external AI model is invoked to produce any progress field"*.

### 2.2 Scanner progress extraction

`scanner.py` gains an `_extract_progress(jsonl_path, cwd)` helper that,
given a transcript file:

1. Streams the file and keeps a rolling window of the last 50 non-empty
   JSONL lines (bounded to avoid loading huge transcripts fully). The
   window contains EXACTLY the last 50 non-empty lines when the
   transcript has ≥ 50 non-empty lines, and all of them otherwise.
2. From this tail:
   - `last_user_prompt` = text of the most recent `type == "user"`
     line's message content, first line, truncated.
   - `last_assistant_summary` = text of the most recent
     `type == "assistant"` line's message content, first line,
     truncated. Supports both string content and content-part arrays
     (`[{"type":"text","text":"..."}]` — tool_use parts are skipped
     for the summary, only text parts are joined).
   - `current_task_hint` = built from the most recent `tool_use`
     content-part anywhere in the tail window, per the rules in §2.1.
3. **"Fresher wins" rule for `last_user_prompt` (binding — option (b)
   from amendment §11.5).** The scanner overwrites
   `last_user_prompt` only when ALL of the following hold:
   (i) the extracted value is a non-empty string;
   (ii) it differs from the stored `last_user_prompt`;
   (iii) the transcript file's mtime (as an aware UTC datetime) is
   strictly newer than the record's stored `last_activity_at`.
   The hook path (§2.3) always bumps `last_activity_at` to "now"
   when it writes the prompt, so a hook-written prompt is protected
   until a later transcript actually contains a newer user line
   that Claude Code has flushed to disk. When any of the three
   conditions is false, the stored `last_user_prompt` is preserved
   verbatim.
4. `last_assistant_summary` and `current_task_hint` are ALWAYS
   overwritten by the scanner on every pass (they have no hook
   writer, so "fresher wins" is trivially "scanner wins"). This
   matches §2.1's scanner-owned contract.

Error isolation: any decode / unicode / key error while extracting
progress from a single transcript MUST NOT crash the scan. It logs one
warning line to `~/.claude/claude-tasks/.scanner-errors.log` and leaves
all three fields unchanged from their prior values.

**Unicode / non-UTF-8 handling (binding).** The file is opened with
`encoding="utf-8", errors="replace"` so a bad byte yields U+FFFD but
never raises. Truncation operates on Python string code points, not
bytes, so a multi-byte CJK prompt is truncated at the correct
character boundary and the `"…"` suffix remains a single code point.

### 2.3 Hook-driven progress refresh

`cst hook activity` (UserPromptSubmit) is enhanced: in addition to
bumping `last_activity_at`, it now:

- Parses stdin JSON payload FIRST (Sprint 1 contract is preserved).
- If the payload includes a `prompt` or `user_prompt` string field
  (Claude Code's UserPromptSubmit hook payload — see risk §10.1),
  write it into `last_user_prompt` (truncated to 100 chars + `"…"`).
  When both scanner and hook populate `last_user_prompt`, the
  **fresher write wins** (whichever runs last). We do not tie-break;
  this is acceptable because both signals represent the same
  underlying fact and Claude Code triggers the hook before the
  scanner sees the new JSONL line in the common case.
- Never writes `last_assistant_summary` or `current_task_hint` from a
  hook (those require the transcript; the scanner remains the sole
  source).
- **Unknown session_id behavior (binding — option (a) from amendment
  §11.7).** When the record for `session_id` does not yet exist on
  disk, the hook CREATES a skeleton record containing just the
  normally-defaulted fields from `new_record(sid)` plus
  `last_activity_at = now` and — when the payload supplied one —
  the truncated `last_user_prompt`. Rationale: Sprint 1's
  session-start hook already creates on first sight; if that hook
  missed for any reason, the activity hook's create-skeleton path is
  a cheap safety net so the user's next `cst list` still shows the
  session. The skeleton has `auto_detected=True`, empty
  `cwd`/`project_name`, and null `terminal.*` — the next scanner or
  session-start hook fills them in. Tested by
  `test_activity_hook_on_unknown_session_id`.
- Exits 0 on failure per Sprint 1 contract; the registry must remain
  consistent (no partial writes).

Satisfies DoD: *"After a user submits a prompt in a Claude Code
session, `cst list` within roughly 2 seconds shows that prompt's
text …"*.

### 2.4 `cst list` multi-line display + `--compact`

Default (no `--compact`):

- Headline row exactly as Sprint 1, extended with a new leading
  **live dot** column (see §2.5):
  `<dot>\t<short_id>\t<priority>\t<status>\t<title>\t<project_name>\t<relative_time>`
- If `last_user_prompt` is non-empty, emit a sub-row:
  `\t⤷ <last_user_prompt>`  (leading tab for indent; marker `⤷` is
  U+2937).
- If `current_task_hint` is non-empty, emit a sub-row:
  `\t⚙ <current_task_hint>`  (`⚙` is U+2699).
- Empty sub-rows are OMITTED entirely (not rendered as blank lines).
- `last_assistant_summary` is NOT shown in `cst list` — it is watch-TUI
  (Sprint 3) only. This is per spec §6.1 vs §6.2.

`--compact` returns to a single-line-per-session format identical to
Sprint 1's output **extended only with the new live dot column**.
No sub-rows. Binding: a grep for any of `⤷`, `⚙`, or a second line
per session MUST find zero occurrences under `--compact`.

`--json` (already a Sprint 1 deliverable) gains four new keys per
record: `last_user_prompt`, `last_assistant_summary`,
`current_task_hint`, `live`. Schema compatibility: all pre-existing
keys remain present.

Stale banner: when any session in the result set has derived state
`stale`, a footer line
`⚠ N stale sessions — run 'cst review-stale'` is appended to stdout
after the rows. Under `--json` the footer is omitted; callers can
derive the count themselves.

Satisfies DoD: *"cst list rows are ordered by priority (high → low)
then by most-recent activity"* (unchanged), *"A session whose last
activity is set to 5 hours ago is shown as stale in cst list"*,
*"A footer banner appears in cst list whenever any stale sessions
exist"*, *"cst list --compact returns to a single-line-per-session
format"*, *"cst list … within roughly 2 seconds shows that prompt's
text (truncated to ~100 chars) as a dim ⤷ … sub-row"*.

### 2.5 Live-vs-idle dot

`●` when a `claude` process is currently attached to the record's
`terminal.tty`; `○` otherwise.

Detection via `ps -o pid,tty,comm -A` (macOS-compatible), invoked
once per list invocation (cached for the lifetime of the process).

**`ps` output parsing rule (binding).** Every line after the header
is parsed via `line.split(None, 2)` — i.e. split on whitespace into
at most 3 parts. The three columns are interpreted as:

1. `pid` — must match `^[0-9]+$`; lines that don't are skipped.
2. `tty` — the short tty name. Interpreted per these cases:
   - Literal `?`, `??`, or `-` → the process has no controlling
     tty, skip.
   - `ttysNNN` / `ttyN` / `pts/NNN` → prepend `/dev/` to form the
     absolute device path.
   - Already starts with `/dev/` → used as-is.
3. `comm` — the remainder of the line (may contain spaces because
   `split(None, 2)` caps at 3 parts). We match by
   `os.path.basename(comm.rstrip()) == "claude"`. Note this matches
   only when the command's basename is literally `claude` — a
   process running `node /path/claude-cli.js` has basename `node`
   and is NOT matched.

Fallbacks:

- When a record's `terminal.tty` is null / empty → render `○`
  unconditionally.
- `subprocess` failure (missing binary, non-zero exit, timeout,
  decode error) → the entire listing silently degrades to `○` for
  every row; a single log line is appended to `.scanner-errors.log`.
- A listing must NEVER fail because of `ps` problems.

Added to `--json` as boolean `"live"`.

Satisfies DoD: *"Rows whose tty has a live `claude` process are
marked live; others are marked idle."*

### 2.6 Stale detection + `cst review-stale`

Stale is a **derived, view-only** state. It is computed at list time,
never stored.

Rule: `stale == (not archived) and (status in {in_progress, blocked,
waiting}) and (now - last_activity_at > stale_threshold)`.

- Threshold source (in order): `CST_STALE_THRESHOLD_SECONDS` env var
  (tests), then the config file (§2.10), then default 4 hours
  (14400 seconds).
- `cst list --stale` filters to stale rows only.
- `cst list` (default) displays the word `stale` in the status column
  for stale rows, even though their stored `status` is one of the
  active values. Binding: when a row is stale, the displayed status
  string is literally `stale`; the underlying stored `status` is
  unchanged on disk.

`cst review-stale`:

- Reads stdin one line at a time, presenting each stale session in
  order (priority then recency). For each:
  - Prints `[N/M] <short_id> <title> (<project>) — idle <relative>`
    followed by a prompt `keep/done/archive/skip [k/d/a/s]:`.
  - Reads one line from stdin. Case-insensitive. Accepts
    `k|keep|K`, `d|done|D`, `a|archive|A`, `s|skip|S`. Anything else
    re-prompts the same session (up to 3 times, then skips to avoid
    infinite loops in broken scripts).
- Actions:
  - `keep` / `skip`: binding — record file must be byte-identical
    before and after (SHA256 in tests).
  - `done`: sets `status=done`, flips `auto_detected=false`.
  - `archive`: sets `archived=true`, `archived_at=now`.
- Exits 0 when all stale sessions are reviewed; exits 0 with a "no
  stale sessions" message when there are none.
- Non-interactive mode: when stdin is not a TTY and no input remains,
  the command treats the remaining sessions as `skip` and exits 0.

Satisfies DoD: *"A session whose last activity is set to 5 hours ago
is shown as stale"*, *"`cst review-stale` presents each stale session
in turn and accepts keep / done / archive / skip; choosing "skip" or
"keep" never modifies the record"*, *"No code path ever transitions
a session to archived or deletes a record without explicit user
action"*.

### 2.7 Short-id prefix matching (≥6 chars)

Every CLI path that takes a session id (`set`, `done`, `archive`,
`focus`, `resume`) now accepts a prefix of the UUID ≥ 6 characters.

Resolution rules (binding):

- Exact full-UUID match still takes precedence (zero ambiguity
  possible).
- Prefix < 6 chars: error `cst: session id must be the full UUID or a
  prefix of at least 6 hex characters` → exit 2 (distinct from
  "not found" to catch user typos).
- Prefix ≥ 6, matches exactly one record → success.
- Prefix ≥ 6, matches multiple records → exit 3, writes to stderr:
  ```
  cst: ambiguous prefix '<input>'; candidates:
    <short_id>  <priority>  <title>  (<project>)
    <short_id>  <priority>  <title>  (<project>)
    ...
  ```
  **No record is mutated in the ambiguous case.** Tests assert the
  registry is byte-identical before/after.
- Prefix ≥ 6, matches zero records → exit 1
  (`cst: no such session: <input>`).

**Ordering (binding).** The prefix resolver runs BEFORE any
command-specific side effect, for every subcommand that accepts
an id: `set`, `done`, `archive`, `focus`, `resume`. In particular
for `focus` and `resume` the resolver runs before the macOS
platform guard of §2.11, so an ambiguous id on Linux exits 3
(ambiguity) rather than 6 (platform). Tests cover both orders
(`test_prefix_ambiguous_exits_3_and_does_not_mutate` is
parametrized over all five subcommands).

**Candidate list stability.** The candidate list emitted on
ambiguity is sorted by priority (high→medium→low) then by
`last_activity_at` desc — same order as `cst list`. This is the
order tested in Check 12b.

Satisfies DoD: *"Any command that accepts a session id accepts a 6+
character prefix"*, *"An ambiguous prefix lists candidate sessions
and exits non-zero without mutating anything."*

### 2.8 `cst focus` and `cst resume`

`cst focus <id>` (macOS only — see §2.11):

- Resolves the record. Inspects `terminal.app`:
  - `iTerm.app` or `iTerm2`: run
    `osascript -e 'tell application "iTerm2" to select ...'` using
    the stored `window_id` (int). If `window_id` is null, fall back
    to activating iTerm2 only.
  - `Apple_Terminal` or `Terminal`: run AppleScript targeting the
    stored `window_id` + `tab_id` (either may be null — in which case
    we just activate Terminal.app).
  - `Ghostty`, `Alacritty`, `WezTerm`, `kitty`, or any unrecognised
    `terminal.app`: print
    `cst: focus unsupported for terminal '<app>'. Try: cst resume <short_id>`
    to stderr, exit 4. Do not attempt any AppleScript.
  - `terminal.app` missing / null: same "unsupported" message with
    `<app>` shown as `unknown`.
- osascript exit != 0: print
  `cst: focus failed (<app> window may be closed). Try: cst resume <short_id>`
  to stderr, exit 5.
- Successful osascript: exit 0, no stdout.

`cst resume <id>`:

- Resolves the record. Must have a non-empty `cwd` — otherwise error
  `cst: cannot resume: no cwd recorded for this session` exit 1.
- Spawns a new iTerm2 window (default) that runs
  `cd <cwd> && claude --resume <session_id>`. Implementation uses
  `osascript` to create the window. When iTerm2 is not installed,
  falls back to Terminal.app. When neither is available, prints
  `cst: no supported terminal for resume; install iTerm2 or Terminal.app`
  and exits 4.
- The command does NOT wait for `claude` to start; it returns 0 as
  soon as the AppleScript completes.

**Prefix-resolution ordering (binding).** For BOTH `cst focus` and
`cst resume` the execution order is:

1. Parse argv; ensure id argument is present.
2. Run the short-id resolver from §2.7. If it returns
   `TOO_SHORT` → exit 2. `AMBIGUOUS` → exit 3 (print candidates).
   `NOT_FOUND` → exit 1.
3. Only after resolution succeeds, check the macOS platform guard
   (§2.11). Non-darwin → exit 6.
4. Only then inspect `terminal.app` and run AppleScript.

This means an ambiguous prefix passed to `cst focus` on Linux still
exits 3 (ambiguity), not 6 (platform) — because the user clearly
typo'd and telling them the typo is more useful than a platform
lecture. Tests cover both orders.

**Pinned osascript templates (binding).** The production code
generates exactly these strings via f-string interpolation. Tests
assert byte-for-byte equality (modulo the interpolated values)
against these templates. Drift in the template BREAKS a test.

`focus.py` builds one `-e` argument (one full AppleScript program).
`_run_osascript(args)` prepends `["osascript"]` and shells out.

- **iTerm2 focus, `window_id` is a non-null int `W`:**
  ```applescript
  tell application "iTerm2"
      activate
      tell window id <W> to select
  end tell
  ```
  (where `<W>` is the Python `int` interpolated as decimal digits
  only — see "Escaping / injection hardening" below.)

- **iTerm2 focus, `window_id` is null:**
  ```applescript
  tell application "iTerm2" to activate
  ```

- **Terminal.app focus, `window_id` is int `W` AND `tab_id` is int `T`:**
  ```applescript
  tell application "Terminal"
      activate
      set index of window id <W> to 1
      tell window id <W> to set selected tab to tab <T>
  end tell
  ```

- **Terminal.app focus, `window_id` is int `W`, `tab_id` is null:**
  ```applescript
  tell application "Terminal"
      activate
      set index of window id <W> to 1
  end tell
  ```

- **Terminal.app focus, `window_id` is null:**
  ```applescript
  tell application "Terminal" to activate
  ```

`resume.py`:

- **iTerm2 resume** (preferred; iTerm2 detected by
  `osascript -e 'id of application "iTerm2"'` probe returning 0):
  ```applescript
  tell application "iTerm2"
      activate
      create window with default profile
      tell current session of current window
          write text <SHELL_CMD>
      end tell
  end tell
  ```
  where `<SHELL_CMD>` is the AppleScript-quoted form (see below) of
  the POSIX shell command:
  ```
  cd <CWD_SHELL_Q> && claude --resume <SID_SHELL_Q>
  ```

- **Terminal.app resume** (fallback when iTerm2 not detected):
  ```applescript
  tell application "Terminal"
      activate
      do script <SHELL_CMD>
  end tell
  ```
  with the same `<SHELL_CMD>` building rule.

**Escaping / injection hardening (binding).** Two distinct quoting
layers apply and are NOT interchangeable:

1. **POSIX shell layer** — used for `<CWD_SHELL_Q>` and
   `<SID_SHELL_Q>` inside the `cd ... && claude --resume ...` string.
   Built via Python's `shlex.quote(value)`. This produces
   `'<value>'` with any embedded `'` replaced by `'"'"'`. Example:
   a cwd of `/tmp/a b"c` yields `'/tmp/a b"c'`. No backticks, no
   `$(...)`, no command substitution can survive.

2. **AppleScript string literal layer** — wraps the entire
   shell-command string as an AppleScript quoted literal. Built by:
   `'"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'`.
   The only two characters AppleScript escapes inside a quoted
   string literal are `\` and `"`, in that order. Newlines in the
   value are forbidden (validation raises before AppleScript
   construction); a session_id is validated to match
   `^[0-9a-f-]{36}$` and a cwd is validated to be an absolute path
   string with no embedded `\x00` or newline.

3. **`window_id` and `tab_id` integer hardening** — before
   interpolation into the template, the code calls `int(value)` and
   formats via `f"{n:d}"`. Any non-int (including strings, floats,
   booleans) raises `TypeError` / `ValueError` which `focus.py`
   catches and surfaces as
   `cst: focus failed (corrupt window_id in record). Try: cst resume <short_id>`
   exit 5. There is no code path that interpolates an
   un-int-validated value into the AppleScript body.

**Testability.** AppleScript invocation is behind a thin
`_run_osascript(args: list[str]) -> int` function that tests
monkeypatch to a capturing no-op (returns 0 by default; tests can
configure it to return non-zero to exercise failure paths). Tests
then assert the captured `args[-1]` (the AppleScript program
string) matches the templates above either by exact string equality
or by a compiled regex that allows only the interpolated `<W>` /
`<T>` / `<SHELL_CMD>` slots to vary.

Satisfies DoD: *"cst focus brings the other's window to the front"*
(exercised by test using monkeypatched osascript), *"When the owning
window no longer exists, cst focus does NOT silently fail"*, *"On an
unsupported terminal app, cst focus reports the limitation and offers
cst resume"*, *"cst resume opens a new terminal window, cd's into the
session's cwd, and starts Claude Code with resume against the correct
session"*.

### 2.9 `cst statusline` + installer wiring

`cst statusline`:

- Reads the registry (no JSONL scan, no ps) and computes:
  - `pending = len(records where not archived and status in {in_progress, blocked, waiting})`
  - `stale = len(records satisfying the stale predicate)`
- Emits exactly one line to stdout:
  - `📋 <pending> pending  →  /tasks` when `stale == 0 and pending > 0`
  - `📋 <pending> pending · <stale> stale  →  /tasks` when `stale > 0 and pending > 0`
  - Empty line (newline only) when `pending == 0 and stale == 0` —
    per spec, the statusline is shown only "whenever there is at
    least one pending session"; we still exit 0.
- Performance budget (binding): must complete in ≤ 150 ms on a
  registry of 200 records (pytest timing test seeds 200 records and
  asserts wall time < 0.15 s). No subprocess calls, no network, no
  JSONL parsing.

Installer wiring (idempotent, policy-aware):

- If `settings.json` has no `statusLine` key: set
  `"statusLine": {"type": "command", "command": "cst statusline", "padding": 0}`.
- If `settings.json` already has a `statusLine` key whose `command`
  equals `"cst statusline"` (exact string match): no-op.
- If `settings.json` already has any other `statusLine`: do NOT
  overwrite. Print a warning containing the literal phrase
  `existing statusline` to stdout plus the shell snippet the user can
  add to their own statusline wrapper to append `$(cst statusline)`.
- Malformed `settings.json` continues to trigger Sprint 1 policy (a):
  exit 2, no write, no backup.

Satisfies DoD: *"The Claude Code statusline displays 📋 N pending …"*,
*"The statusline call returns quickly enough not to visibly delay …"*,
*"The statusline command never performs network I/O or heavyweight
parsing"*, and the non-goal *"Silent overwrite of an existing user
statusline configuration"*.

### 2.10 Config file for stale threshold

Location: `~/.claude/claude-tasks.config.json`. Overridable via
`CST_CONFIG_PATH` env var for tests.

Format:
```json
{ "stale_threshold_seconds": 3600 }
```

Only one key is honored this sprint. Loader rules:

- Missing file → default 14400 seconds.
- File is empty or `{}` → default.
- File parses but lacks `stale_threshold_seconds` → default.
- `stale_threshold_seconds` is not an int (bool / float / str / null /
  list / dict) → default; log one warning to `.scanner-errors.log`
  (same log file used by §2.2). Binding: `bool` is rejected even
  though `isinstance(True, int)` is `True` in Python — the loader
  uses `type(v) is int`.
- `stale_threshold_seconds` is an int ≤ 0 (i.e. `0`, `-1`, any
  negative) → default; log one warning. Binding: the threshold must
  be strictly positive to be honored.
- Malformed JSON → default; log one warning. Binding: `cst list`
  MUST NEVER fail because of a malformed config file.

Precedence: `CST_STALE_THRESHOLD_SECONDS` env var (tests only) >
config file > default.

Satisfies DoD: *"The user can override the stale threshold via a
single local config value; setting it to 1 hour causes a session idle
for 90 minutes to be flagged stale on the next list."*

### 2.11 macOS platform guard

Applies to `cst focus` and `cst resume` only. All other subcommands
remain cross-platform (they are stdlib-only and do not touch
AppleScript / `ps` in a macOS-specific way — `ps` on Linux supports
the same `-o pid,tty,comm -A` form but the live-dot detection is
allowed to degrade to `○` silently on non-macOS, per §2.5).

Rule:
- `cst focus` and `cst resume` check `sys.platform == "darwin"`
  first. If not darwin, print
  `cst: <subcommand> is only supported on macOS (detected: <platform>)`
  to stderr and exit 6.
- All other subcommands remain platform-agnostic.

Tests drive the non-darwin branch via `CST_FORCE_PLATFORM=linux` on
the environment.

Satisfies DoD: *"Everything works on macOS. The tool is permitted to
refuse to run on non-macOS platforms with a clear message."*

### 2.12 `cst gc`

- Scans the registry.
- For each record where `archived is True` and `archived_at` is an
  ISO-8601 UTC timestamp older than 7 days (604800 seconds), delete
  the file via `os.unlink`. Records that are not archived are
  **never** touched.
- Binding: age is computed from the stored `archived_at`; file mtime
  is ignored.
- Prints `cst gc: deleted N record(s); kept M archived record(s)
  still within the 7-day window`. Non-archived records are not
  reported in the summary.
- Exit 0 on success (including "nothing deleted"). Exit 1 on I/O
  error — message to stderr, and the scan completes as much as
  possible (a single failed unlink does not abort the pass;
  remaining files are still processed and the final exit is 1).
- Unparseable `archived_at` → skip the record, log one line to
  `.scanner-errors.log`, do NOT delete.

Satisfies DoD: *"cst gc deletes only records whose archived_at is
older than 7 days and never touches any other record"*, *"No
background process, hook, or scan ever deletes a record"*.

## 3. Features explicitly deferred

| Feature | Target sprint |
|---|---|
| Slash commands (`/tasks`, `/task-register`, `/task-note`, `/task-priority`, `/task-status`, `/task-done`, `/task-focus`) | Sprint 3 |
| `cst watch` TUI (rich-based) incl. detail panel with full progress fields | Sprint 3 |
| `cst watch --pin` dedicated small window | Sprint 3 |

Rationale: slash commands are thin wrappers around the already-built
CLI, but they require shipping `commands/*.md` files and an install
step that writes them into the skill; they naturally belong with the
watch TUI which is the remaining "inside / TUI" theme. Sprint 2
deliberately focuses on headless CLI + infra so every Sprint 3
surface builds on a stable foundation.

(Focus / resume / statusline are IN Sprint 2 despite one reading of
them as "inside Claude Code" — they are invoked by the user, CLI-
shaped, and unlock the Sprint 2 "daily driver" promise. The slash
commands remain deferred because they require `commands/*.md`
shipping and an installer merge story that pairs well with the TUI
work.)

## 4. How to run

All commands assume `cd` into the skill dir and a sandbox HOME unless
stated otherwise.

### 4.1 Install (idempotent; picks up Sprint 2 changes)

```bash
bash install.sh
```

Expected additions vs Sprint 1:
- Exactly one `cst statusline` entry in `settings.json.statusLine`
  (unless a competing one already exists, in which case a warning is
  printed and the existing value is preserved).
- `~/.claude/claude-tasks.config.json` is NOT auto-created; it is
  strictly opt-in.

### 4.2 Simulate progress-capture end-to-end

```bash
SID="66666666-6666-6666-6666-666666666666"
mkdir -p ~/.claude/projects/-tmp-demo
cat > ~/.claude/projects/-tmp-demo/${SID}.jsonl <<'EOF'
{"type":"user","message":{"content":"Please run the test suite"},"cwd":"/tmp/demo"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Running the tests now."},{"type":"tool_use","name":"Bash","input":{"command":"pytest -q tests/"}}]}}
EOF
cst scan
cst list            # multi-line: ⤷ prompt, ⚙ Running: pytest -q tests/
cst list --compact  # single line per session, no ⤷ / ⚙
cst list --json     # includes last_user_prompt, last_assistant_summary, current_task_hint, live
```

### 4.3 Trigger the UserPromptSubmit hook with a prompt payload

```bash
echo '{"session_id":"'"$SID"'","hook_event_name":"UserPromptSubmit","prompt":"Now also add a fixture"}' \
    | cst hook activity
cst list --json | python3 -c "import sys,json; r=json.load(sys.stdin); print([x['last_user_prompt'] for x in r if x['session_id']==\"$SID\"])"
# Expect: ['Now also add a fixture']
```

### 4.4 Stale triage

```bash
python3 -c "
import json, pathlib, os, datetime
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json')
r = json.loads(p.read_text())
r['last_activity_at'] = (datetime.datetime.now(datetime.timezone.utc)
    - datetime.timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
p.write_text(json.dumps(r))
"
cst list             # banner: ⚠ 1 stale sessions
cst list --stale     # only that row
echo "skip" | cst review-stale   # non-interactive skip
```

### 4.5 Short-id prefix

```bash
cst set 66666666 --priority high      # 8-char prefix exact
cst set 66 --priority low             # exit 2, "must be ≥6 chars"
# Seed a second session sharing a prefix, then:
cst set 666666 --priority high        # exit 3, lists candidates
```

### 4.6 Focus / resume (macOS)

On a macOS machine with iTerm2 running:

```bash
cst focus 66666666
cst resume 66666666
```

Tests monkeypatch `_run_osascript`; no AppleScript actually runs in
CI.

### 4.7 Statusline

```bash
cst statusline          # "📋 2 pending" or "📋 2 pending · 1 stale  →  /tasks"
time cst statusline     # must be < 150 ms on 200 records
```

### 4.8 GC

```bash
cst archive 66666666
# artificially age archived_at to 8 days ago, then:
cst gc                  # deletes the file
```

## 5. Observable verification checks

Each check is stricter than the corresponding DoD bullet. All
commands assume a fresh `HOME=$(mktemp -d)` sandbox with `bash
install.sh` already run and `~/.local/bin` on PATH.

**Numbering note.** To minimize diff against the prior draft, new
checks from the Evaluator amendments are added in-place:
- Check 12b — ambiguous prefix on every subcommand (amendment §11.3).
- Check 21b — `cst gc` on empty / all-non-archived registry (§11.4).
- Checks 28, 29, 30 — inserted AFTER Check 26 and BEFORE Check 27
  (the Sprint-1 regression check that deliberately remains last).
  28 covers unicode display (§11.8), 29 covers "fresher wins"
  (§11.5), 30 covers config loader rejecting zero / negative / bool
  (§11.11).

All existing Check 1–27 numbers are preserved.

### Check 1 — Scanner extracts and WRITES all three progress fields

```bash
SID=66666666-6666-6666-6666-666666666666
mkdir -p "$HOME/.claude/projects/-tmp-demo"
cat > "$HOME/.claude/projects/-tmp-demo/${SID}.jsonl" <<'EOF'
{"type":"user","message":{"content":"Please run the test suite and fix the failures"},"cwd":"/tmp/demo"}
{"type":"assistant","message":{"content":[{"type":"text","text":"OK. First I will run pytest."},{"type":"tool_use","name":"Bash","input":{"command":"pytest -q tests/"}}]}}
EOF
cst scan >/dev/null
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json').read_text())
assert r['last_user_prompt'].startswith('Please run the test suite'), r
assert r['last_assistant_summary'].startswith('OK. First I will run pytest'), r
assert r['current_task_hint'] == 'Running: pytest -q tests/', r
print('PROGRESS_EXTRACT_OK')
"
```
**Expected:** prints `PROGRESS_EXTRACT_OK`.

### Check 2 — Scanner ALWAYS overwrites progress fields (auto_detected=false does NOT protect them)

```bash
cst set $SID --title "User Title" --priority high   # flips auto_detected=false
# Overwrite progress fields with garbage values on disk:
python3 -c "
import json, pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json')
r = json.loads(p.read_text())
r['last_user_prompt'] = 'GARBAGE'
r['last_assistant_summary'] = 'GARBAGE'
r['current_task_hint'] = 'GARBAGE'
p.write_text(json.dumps(r))
"
cst scan >/dev/null
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json').read_text())
assert r['title'] == 'User Title', r     # user-owned: preserved
assert r['priority'] == 'high', r         # user-owned: preserved
assert r['last_user_prompt'] != 'GARBAGE', r
assert r['last_assistant_summary'] != 'GARBAGE', r
assert r['current_task_hint'] == 'Running: pytest -q tests/', r
print('SCANNER_OVERWRITES_PROGRESS_OK')
"
```
**Expected:** prints `SCANNER_OVERWRITES_PROGRESS_OK`.

### Check 3 — Truncation to 100 chars with single `…` code point

```bash
python3 -c "
import json, pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/projects/-tmp-demo/${SID}.jsonl')
big = 'X' * 500
p.parent.mkdir(parents=True, exist_ok=True)
with p.open('w') as f:
    f.write(json.dumps({'type':'user','message':{'content': big},'cwd':'/tmp/demo'}) + '\n')
"
cst scan >/dev/null
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json').read_text())
p = r['last_user_prompt']
assert len(p) == 100, (len(p), p)
assert p.endswith('\u2026'), repr(p)
assert p.count('\u2026') == 1, p
print('TRUNCATION_OK')
"
```
**Expected:** prints `TRUNCATION_OK`.

### Check 4 — Unicode / CJK prompt truncates on code-point boundaries

```bash
python3 -c "
import json, pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/projects/-tmp-demo/${SID}.jsonl')
big = '한' * 500   # 3-byte UTF-8 char, 1 code point each
with p.open('w') as f:
    f.write(json.dumps({'type':'user','message':{'content': big},'cwd':'/tmp/demo'}) + '\n')
"
cst scan >/dev/null
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json').read_text())
p = r['last_user_prompt']
assert len(p) == 100, (len(p), p)
assert p.endswith('\u2026')
assert all(c == '한' for c in p[:99]), p[:10]
print('CJK_TRUNCATION_OK')
"
```
**Expected:** prints `CJK_TRUNCATION_OK`.

### Check 5 — Non-UTF-8 bytes do not crash the scanner

```bash
python3 -c "
import pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/projects/-tmp-demo/${SID}.jsonl')
p.write_bytes(b'{\"type\":\"user\",\"message\":{\"content\":\"ok \xff\xfe bad\"},\"cwd\":\"/tmp/demo\"}\n')
"
cst scan; rc=$?
[ "$rc" = "0" ] || { echo "FAIL_SCAN_RC=$rc"; exit 1; }
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json').read_text())
assert isinstance(r['last_user_prompt'], str)
assert 'ok' in r['last_user_prompt']
print('NON_UTF8_OK')
"
```
**Expected:** prints `NON_UTF8_OK` and exits 0.

### Check 6 — `current_task_hint` variants (exhaustive)

```bash
python3 - <<'PY'
import json, os, pathlib, subprocess
SID = '66666666-6666-6666-6666-666666666666'
home = pathlib.Path(os.environ['HOME'])
proj = home / '.claude/projects/-tmp-demo'
proj.mkdir(parents=True, exist_ok=True)
cases = [
    ('Bash',  {'command':'pytest -q'},        'Running: pytest -q'),
    ('Edit',  {'file_path':'/tmp/demo/a.py'}, 'Editing: a.py'),
    ('Write', {'file_path':'/tmp/demo/x.md'}, 'Editing: x.md'),
    ('MultiEdit',    {'file_path':'/tmp/demo/m.py'}, 'Editing: m.py'),
    ('NotebookEdit', {'file_path':'/tmp/demo/n.ipynb'}, 'Editing: n.ipynb'),
    ('Read',  {'file_path':'/tmp/demo/b.py'}, 'Reading: b.py'),
    ('Grep',  {'pattern':'TODO'},             'Searching: TODO'),
    ('Glob',  {'pattern':'**/*.py'},          'Searching: **/*.py'),
    ('UnknownTool', {}, 'UnknownTool'),
    ('Bash',  {}, 'Bash'),   # missing distinguishing field → bare tool name
]
jf = proj / f'{SID}.jsonl'
for tool, inp, expected in cases:
    jf.write_text(
        json.dumps({'type':'user','message':{'content':'q'},'cwd':'/tmp/demo'}) + '\n'
        + json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','name':tool,'input':inp}]}}) + '\n'
    )
    subprocess.run(['cst','scan'], check=True, capture_output=True)
    rec = json.loads((home / f'.claude/claude-tasks/{SID}.json').read_text())
    assert rec['current_task_hint'] == expected, (tool, inp, rec['current_task_hint'], expected)
# No tool_use at all → empty hint:
jf.write_text(
    json.dumps({'type':'user','message':{'content':'q'},'cwd':'/tmp/demo'}) + '\n'
    + json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'just text'}]}}) + '\n'
)
subprocess.run(['cst','scan'], check=True, capture_output=True)
rec = json.loads((home / f'.claude/claude-tasks/{SID}.json').read_text())
assert rec['current_task_hint'] == '', rec['current_task_hint']
print('TASK_HINT_OK')
PY
```
**Expected:** prints `TASK_HINT_OK`.

### Check 7 — Multi-line `cst list` renders `⤷` and `⚙`

```bash
python3 -c "
import json, pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/projects/-tmp-demo/${SID}.jsonl')
p.write_text(json.dumps({'type':'user','message':{'content':'Fix bug'},'cwd':'/tmp/demo'}) + '\n'
             + json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'pytest -q'}}]}}) + '\n')
"
cst scan >/dev/null
out=$(cst list)
echo "$out" | grep -F '⤷ Fix bug' >/dev/null || { echo FAIL_NO_PROMPT_SUBROW; exit 1; }
echo "$out" | grep -F '⚙ Running: pytest -q' >/dev/null || { echo FAIL_NO_HINT_SUBROW; exit 1; }
echo MULTILINE_OK
```
**Expected:** prints `MULTILINE_OK`.

### Check 8 — `--compact` strips all sub-rows; one line per session

```bash
out=$(cst list --compact)
[ "$(echo "$out" | grep -c -F '⤷')" = "0" ] || { echo FAIL_PROMPT_LEAK; exit 1; }
[ "$(echo "$out" | grep -c -F '⚙')" = "0" ] || { echo FAIL_HINT_LEAK; exit 1; }
n_sessions=$(cst list --json | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
n_lines=$(echo "$out" | sed '/^$/d' | wc -l | tr -d ' ')
[ "$n_sessions" = "$n_lines" ] || { echo "FAIL_ROW_COUNT: $n_sessions vs $n_lines"; exit 1; }
echo COMPACT_OK
```
**Expected:** prints `COMPACT_OK`.

### Check 9 — Hook writes `last_user_prompt` from stdin payload

```bash
echo '{"session_id":"'"$SID"'","hook_event_name":"UserPromptSubmit","prompt":"hook-provided prompt"}' \
    | cst hook activity
got=$(cst list --json | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    if r['session_id'] == '${SID}':
        print(r['last_user_prompt']); break
")
[ "$got" = "hook-provided prompt" ] || { echo "FAIL: got='$got'"; exit 1; }
echo HOOK_PROMPT_OK
```
**Expected:** prints `HOOK_PROMPT_OK`.

### Check 10 — Stale banner + filter + stored status UNCHANGED

```bash
python3 -c "
import json, pathlib, os, datetime
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json')
r = json.loads(p.read_text())
r['status'] = 'in_progress'
r['archived'] = False
r['last_activity_at'] = (datetime.datetime.now(datetime.timezone.utc)
    - datetime.timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
p.write_text(json.dumps(r))
"
out=$(cst list)
echo "$out" | grep -q '^⚠' || { echo FAIL_NO_BANNER; exit 1; }
echo "$out" | grep -q 'stale' || { echo FAIL_NO_STALE_LABEL; exit 1; }
cst list --stale | grep -q "${SID:0:8}" || { echo FAIL_STALE_FILTER; exit 1; }
stored=$(python3 -c "
import json, os, pathlib
print(json.load(open(pathlib.Path(os.environ['HOME'],'.claude/claude-tasks/${SID}.json')))['status'])
")
[ "$stored" = "in_progress" ] || { echo "FAIL: status mutated to $stored"; exit 1; }
echo STALE_OK
```
**Expected:** prints `STALE_OK`.

### Check 11 — `cst review-stale` keep/skip is byte-identical; done mutates; 3-session ordering stable

Phase A — single session (keep/skip byte-identical, done mutates):

```bash
before=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID}.json" | awk '{print $1}')
printf 'keep\n' | cst review-stale
after=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID}.json" | awk '{print $1}')
[ "$before" = "$after" ] || { echo "FAIL: keep modified record ($before -> $after)"; exit 1; }
printf 'skip\n' | cst review-stale
after2=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID}.json" | awk '{print $1}')
[ "$before" = "$after2" ] || { echo "FAIL: skip modified record"; exit 1; }
printf 'done\n' | cst review-stale
after3=$(python3 -c "
import json, os, pathlib
print(json.load(open(pathlib.Path(os.environ['HOME'],'.claude/claude-tasks/${SID}.json')))['status'])
")
[ "$after3" = "done" ] || { echo FAIL_DONE; exit 1; }
```

Phase B — three stale sessions, ordering + advancement (addresses
amendment §11.12). Seed three fresh stale records with distinct
priorities: A=high, B=medium, C=low; all aged 5h ago.

```bash
SID_RA=11110000-1111-1111-1111-111111111111   # high
SID_RB=22220000-2222-2222-2222-222222222222   # medium
SID_RC=33330000-3333-3333-3333-333333333333   # low
python3 - <<'PY'
import json, pathlib, os, datetime
home = pathlib.Path(os.environ['HOME'])
d = home / '.claude/claude-tasks'
aged = (datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
for sid, pri in [('11110000-1111-1111-1111-111111111111','high'),
                 ('22220000-2222-2222-2222-222222222222','medium'),
                 ('33330000-3333-3333-3333-333333333333','low')]:
    (d / f'{sid}.json').write_text(json.dumps({
        'session_id': sid, 'title': f't-{pri}', 'priority': pri,
        'status': 'in_progress', 'cwd': '/tmp', 'project_name': 'p',
        'tags': [], 'note': '',
        'created_at': aged, 'last_activity_at': aged,
        'last_user_prompt': '', 'last_assistant_summary': '', 'current_task_hint': '',
        'terminal': {'app': None, 'window_id': None, 'tab_id': None, 'tty': None},
        'auto_detected': True, 'archived': False, 'archived_at': None,
    }))
PY
ha=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_RA}.json" | awk '{print $1}')
hb=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_RB}.json" | awk '{print $1}')
hc=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_RC}.json" | awk '{print $1}')
# Three "skip" lines: all three sessions presented, none mutated.
out=$(printf 'skip\nskip\nskip\n' | cst review-stale)
# Assert short_id prefixes appear in the exact priority order (A before B before C).
pa=$(echo "$out" | grep -nE "(^|[^0-9a-f])${SID_RA:0:8}" | head -n1 | cut -d: -f1)
pb=$(echo "$out" | grep -nE "(^|[^0-9a-f])${SID_RB:0:8}" | head -n1 | cut -d: -f1)
pc=$(echo "$out" | grep -nE "(^|[^0-9a-f])${SID_RC:0:8}" | head -n1 | cut -d: -f1)
[ -n "$pa" ] && [ -n "$pb" ] && [ -n "$pc" ] || { echo FAIL_NOT_ALL_PRESENTED; exit 1; }
[ "$pa" -lt "$pb" ] && [ "$pb" -lt "$pc" ] || { echo "FAIL_ORDER: a=$pa b=$pb c=$pc"; exit 1; }
# After three skips: all three records still byte-identical.
[ "$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_RA}.json" | awk '{print $1}')" = "$ha" ] || { echo FAIL_RA_MUT; exit 1; }
[ "$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_RB}.json" | awk '{print $1}')" = "$hb" ] || { echo FAIL_RB_MUT; exit 1; }
[ "$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_RC}.json" | awk '{print $1}')" = "$hc" ] || { echo FAIL_RC_MUT; exit 1; }
# Non-interactive short-read: only 1 input line provided → remaining sessions auto-skip, exit 0.
out2=$(printf 'skip\n' | cst review-stale); rc=$?
[ "$rc" = "0" ] || { echo "FAIL_EOF_RC=$rc"; exit 1; }
echo REVIEW_STALE_OK
```
**Expected:** prints `REVIEW_STALE_OK`.

### Check 12 — Short-id prefix ambiguity: candidates listed, exit 3, no mutation

```bash
SID_A=aabbccdd-1111-2222-3333-444455556666
SID_B=aabbccdd-7777-8888-9999-aaaabbbbcccc
for s in $SID_A $SID_B; do
    CLAUDE_SESSION_ID=$s CLAUDE_PROJECT_DIR=/tmp/amb cst hook session-start < /dev/null
done
h1=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_A}.json" | awk '{print $1}')
h2=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_B}.json" | awk '{print $1}')
out=$(cst set aabbccdd --priority high 2>&1); rc=$?
[ "$rc" = "3" ] || { echo "FAIL_EXIT: $rc"; exit 1; }
echo "$out" | grep -q 'ambiguous' || { echo FAIL_NO_WORD; exit 1; }
echo "$out" | grep -q "${SID_A:0:8}" || { echo FAIL_NO_CAND_A; exit 1; }
echo "$out" | grep -q "${SID_B:0:8}" || { echo FAIL_NO_CAND_B; exit 1; }
h1b=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_A}.json" | awk '{print $1}')
h2b=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_B}.json" | awk '{print $1}')
[ "$h1" = "$h1b" ] && [ "$h2" = "$h2b" ] || { echo FAIL_MUTATED; exit 1; }
echo AMBIGUOUS_OK
```
**Expected:** prints `AMBIGUOUS_OK`.

### Check 12b — Ambiguous prefix on every id-accepting subcommand

Addresses amendment §11.3. Uses the same two-record ambiguous
fixture as Check 12; reruns the ambiguity path on `done`, `archive`,
`focus`, `resume`, and re-verifies `set` for symmetry. All five must
exit 3, print `ambiguous`, and leave both record files
byte-identical.

```bash
# Fixture from Check 12 already present; rehash baseline.
h1=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_A}.json" | awk '{print $1}')
h2=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_B}.json" | awk '{print $1}')

for cmd in "set aabbccdd --priority high" "done aabbccdd" "archive aabbccdd" "focus aabbccdd" "resume aabbccdd"; do
    # Force non-darwin so focus/resume CANNOT succeed on ambiguity for a
    # different reason; the resolver MUST still fire first (§2.8 ordering).
    out=$(CST_FORCE_PLATFORM=linux cst $cmd 2>&1); rc=$?
    [ "$rc" = "3" ] || { echo "FAIL_RC $cmd -> $rc"; exit 1; }
    echo "$out" | grep -q 'ambiguous' || { echo "FAIL_MSG $cmd"; exit 1; }
    echo "$out" | grep -q "${SID_A:0:8}" || { echo "FAIL_CANDA $cmd"; exit 1; }
    echo "$out" | grep -q "${SID_B:0:8}" || { echo "FAIL_CANDB $cmd"; exit 1; }
    h1n=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_A}.json" | awk '{print $1}')
    h2n=$(shasum -a 256 "$HOME/.claude/claude-tasks/${SID_B}.json" | awk '{print $1}')
    [ "$h1" = "$h1n" ] && [ "$h2" = "$h2n" ] || { echo "FAIL_MUTATED $cmd"; exit 1; }
done
echo AMBIGUOUS_ALL_CMDS_OK
```
**Expected:** prints `AMBIGUOUS_ALL_CMDS_OK`.

### Check 13 — Short-id prefix: too short

```bash
out=$(cst set aabbc --priority high 2>&1); rc=$?
[ "$rc" = "2" ] || { echo "FAIL_EXIT: $rc"; exit 1; }
echo "$out" | grep -q 'at least 6 hex' || { echo FAIL_MSG; exit 1; }
echo SHORT_PREFIX_OK
```
**Expected:** prints `SHORT_PREFIX_OK`.

### Check 14 — `cst focus` / `cst resume` coverage (via pytest monkeypatch)

```bash
python3 -m pytest -q tests/test_focus.py tests/test_resume.py
```
**Expected:** all tests pass. Tests exercise every branch
(supported terminal, unsupported, osascript failure, macOS guard,
missing cwd, no supported terminal) without invoking real AppleScript.

### Check 15 — `cst focus` on non-macOS exits with platform message

```bash
CST_FORCE_PLATFORM=linux cst focus aabbccdd-1111-2222-3333-444455556666 2>/tmp/focus.err
rc=$?
[ "$rc" = "6" ] || { echo "FAIL_EXIT: $rc"; exit 1; }
grep -q 'only supported on macOS' /tmp/focus.err || { echo FAIL_MSG; exit 1; }
echo PLATFORM_GUARD_OK
```
**Expected:** prints `PLATFORM_GUARD_OK`.

### Check 16 — `cst statusline` output shapes

```bash
rm -rf "$HOME/.claude/claude-tasks"; mkdir -p "$HOME/.claude/claude-tasks"
out=$(cst statusline); [ -z "$out" ] || { echo "FAIL_EMPTY: '$out'"; exit 1; }
CLAUDE_SESSION_ID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee CLAUDE_PROJECT_DIR=/tmp/sl \
    cst hook session-start < /dev/null
out=$(cst statusline)
echo "$out" | grep -qF '📋 1 pending  →  /tasks' || { echo "FAIL_PENDING: '$out'"; exit 1; }
echo "$out" | grep -qF 'stale' && { echo FAIL_UNEXPECTED_STALE; exit 1; } || true
# Amendment §11 polish: arrow must NEVER appear when pending == 0.
rm -rf "$HOME/.claude/claude-tasks"; mkdir -p "$HOME/.claude/claude-tasks"
empty_out=$(cst statusline)
echo "$empty_out" | grep -qF '/tasks' && { echo FAIL_ARROW_ON_EMPTY; exit 1; } || true
# Re-seed the pending session for the rest of the check.
CLAUDE_SESSION_ID=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee CLAUDE_PROJECT_DIR=/tmp/sl \
    cst hook session-start < /dev/null
python3 -c "
import json, pathlib, os, datetime
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json')
r = json.loads(p.read_text())
r['last_activity_at'] = (datetime.datetime.now(datetime.timezone.utc)
    - datetime.timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
p.write_text(json.dumps(r))
"
out=$(cst statusline)
echo "$out" | grep -qF '📋 1 pending · 1 stale  →  /tasks' || { echo "FAIL_STALE: '$out'"; exit 1; }
echo STATUSLINE_OK
```
**Expected:** prints `STATUSLINE_OK`.

### Check 17 — Statusline perf: ≤ 150 ms on 200 records

```bash
python3 -m pytest -q tests/test_statusline.py::test_statusline_200_records_under_150ms
```
**Expected:** test passes.

### Check 18 — Installer wires statusline once; never overwrites existing

```bash
TMPH=$(mktemp -d); mkdir -p "$TMPH/.claude"
echo '{"statusLine":{"type":"command","command":"echo CUSTOM"}}' > "$TMPH/.claude/settings.json"
HOME=$TMPH bash install.sh >/tmp/inst.log 2>&1
grep -q 'existing statusline' /tmp/inst.log || { echo FAIL_NO_WARN; exit 1; }
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path('$TMPH/.claude/settings.json').read_text())
assert s['statusLine']['command'] == 'echo CUSTOM', s
print('PRESERVED')
"

TMPH2=$(mktemp -d)
HOME=$TMPH2 bash install.sh >/dev/null 2>&1
# Amendment §11.9: assert the FULL statusLine object shape, not just command.
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path('$TMPH2/.claude/settings.json').read_text())
sl = s['statusLine']
assert isinstance(sl, dict), sl
assert sl['type'] == 'command', sl
assert sl['command'] == 'cst statusline', sl
assert sl['padding'] == 0, sl
# And no stray keys we don't intend:
assert set(sl.keys()) == {'type', 'command', 'padding'}, sl
print('FRESH_SET')
"

HOME=$TMPH2 bash install.sh >/dev/null 2>&1
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path('$TMPH2/.claude/settings.json').read_text())
sl = s['statusLine']
assert sl['type'] == 'command' and sl['command'] == 'cst statusline' and sl['padding'] == 0, sl
assert set(sl.keys()) == {'type', 'command', 'padding'}, sl
print('IDEMPOTENT_OK')
"
echo STATUSLINE_INSTALL_OK
```
**Expected:** prints `PRESERVED`, `FRESH_SET`, `IDEMPOTENT_OK`,
`STATUSLINE_INSTALL_OK`.

### Check 19 — Config file drives stale threshold

```bash
python3 -c "
import json, pathlib, os, datetime
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json')
r = json.loads(p.read_text())
r['last_activity_at'] = (datetime.datetime.now(datetime.timezone.utc)
    - datetime.timedelta(minutes=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
r['archived'] = False; r['status'] = 'in_progress'
p.write_text(json.dumps(r))
"
cst list --stale | grep -q aaaaaaaa && { echo FAIL_DEFAULT_STALE; exit 1; } || true
echo '{"stale_threshold_seconds": 3600}' > "$HOME/.claude/claude-tasks.config.json"
cst list --stale | grep -q aaaaaaaa || { echo FAIL_CONFIG_NOT_APPLIED; exit 1; }
echo CONFIG_OK
```
**Expected:** prints `CONFIG_OK`.

### Check 20 — Malformed config file does NOT break `cst list`

```bash
echo 'not json' > "$HOME/.claude/claude-tasks.config.json"
cst list >/dev/null; rc=$?
[ "$rc" = "0" ] || { echo "FAIL: cst list broke on bad config (rc=$rc)"; exit 1; }
grep -q 'claude-tasks.config.json' "$HOME/.claude/claude-tasks/.scanner-errors.log" \
    || { echo FAIL_NO_LOG_ENTRY; exit 1; }
echo CONFIG_ROBUST_OK
```
**Expected:** prints `CONFIG_ROBUST_OK`.

### Check 21 — `cst gc` respects the 7-day window

```bash
SID_OLD=11112222-3333-4444-5555-666677778888
SID_NEW=aabbccdd-eeff-0011-2233-445566778899
SID_LIVE=00001111-2222-3333-4444-555566667777
python3 -c "
import json, pathlib, os, datetime
home = pathlib.Path(os.environ['HOME'])
d = home / '.claude/claude-tasks'
def rec(sid, archived, archived_at, status='in_progress'):
    (d / f'{sid}.json').write_text(json.dumps({
        'session_id': sid, 'title': sid[:6], 'priority': 'medium',
        'status': status, 'cwd': '/tmp', 'project_name': 'x',
        'tags': [], 'note': '',
        'created_at': '2025-01-01T00:00:00Z',
        'last_activity_at': '2025-01-01T00:00:00Z',
        'last_user_prompt': '', 'last_assistant_summary': '', 'current_task_hint': '',
        'terminal': {'app': None, 'window_id': None, 'tab_id': None, 'tty': None},
        'auto_detected': False, 'archived': archived, 'archived_at': archived_at,
    }))
now = datetime.datetime.now(datetime.timezone.utc)
rec('${SID_OLD}',  True,  (now - datetime.timedelta(days=8)).strftime('%Y-%m-%dT%H:%M:%SZ'))
rec('${SID_NEW}',  True,  (now - datetime.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ'))
rec('${SID_LIVE}', False, None)
"
out21=$(cst gc); rc=$?
[ "$rc" = "0" ] || { echo "FAIL_RC: $rc"; exit 1; }
[ ! -f "$HOME/.claude/claude-tasks/${SID_OLD}.json" ] || { echo FAIL_OLD_KEPT; exit 1; }
[ -f "$HOME/.claude/claude-tasks/${SID_NEW}.json" ]   || { echo FAIL_NEW_DELETED; exit 1; }
[ -f "$HOME/.claude/claude-tasks/${SID_LIVE}.json" ]  || { echo FAIL_LIVE_DELETED; exit 1; }
# Pin the summary format: "cst gc: deleted <N> record(s); kept <M> archived record(s) still within the 7-day window"
echo "$out21" | grep -qE '^cst gc: deleted 1 record\(s\); kept 1 archived record\(s\) still within the 7-day window$' \
    || { echo "FAIL_SUMMARY: '$out21'"; exit 1; }
echo GC_OK
```
**Expected:** prints `GC_OK`.

### Check 21b — `cst gc` on empty registry and on all-non-archived registry

Addresses amendment §11.4.

```bash
# (a) Registry with zero records at all:
rm -rf "$HOME/.claude/claude-tasks"; mkdir -p "$HOME/.claude/claude-tasks"
out=$(cst gc); rc=$?
[ "$rc" = "0" ] || { echo "FAIL_EMPTY_RC=$rc"; exit 1; }
echo "$out" | grep -qE '^cst gc: deleted 0 record\(s\); kept 0 archived record\(s\) still within the 7-day window$' \
    || { echo "FAIL_EMPTY_MSG: '$out'"; exit 1; }

# (b) Registry with only non-archived records:
CLAUDE_SESSION_ID=dddddddd-dddd-dddd-dddd-dddddddddddd CLAUDE_PROJECT_DIR=/tmp/g \
    cst hook session-start < /dev/null
rec_path=$(ls "$HOME/.claude/claude-tasks/"dddddddd-*.json | head -n1)
before=$(shasum -a 256 "$rec_path" | awk '{print $1}')
out=$(cst gc); rc=$?
after=$(shasum -a 256 "$rec_path" | awk '{print $1}')
[ "$rc" = "0" ] || { echo "FAIL_NOARCH_RC=$rc"; exit 1; }
[ "$before" = "$after" ] || { echo FAIL_NOARCH_MUTATED; exit 1; }
echo "$out" | grep -qE '^cst gc: deleted 0 record\(s\); kept 0 archived record\(s\) still within the 7-day window$' \
    || { echo "FAIL_NOARCH_MSG: '$out'"; exit 1; }
echo GC_EMPTY_OK
```
**Expected:** prints `GC_EMPTY_OK`.

### Check 22 — Live-vs-idle dot via `ps` monkeypatch (pytest)

```bash
python3 -m pytest -q tests/test_live_dot.py
```
**Expected:** passes. Tests inject a fake `ps` output via subprocess
mocking and assert the dot for matching / non-matching / no-tty rows.

### Check 23 — `--json` schema compatibility across `--all` / `--stale`; banner omitted

Addresses amendment §11.6. Verifies that the JSON surface parses
cleanly under every combination of filter flags, the schema keys are
present for every row in every combination, and the stale banner /
any non-JSON chrome are absent from JSON output.

```bash
for flags in "" "--all" "--stale" "--all --stale"; do
    out=$(cst list $flags --json)
    # 1. Parses as JSON.
    echo "$out" | python3 -c "import sys,json; json.load(sys.stdin)" \
        || { echo "FAIL_JSON_PARSE flags='$flags'"; exit 1; }
    # 2. No banner / chrome leaked into JSON.
    echo "$out" | grep -qF '⚠' && { echo "FAIL_BANNER_LEAK flags='$flags'"; exit 1; } || true
    echo "$out" | grep -qF "run 'cst review-stale'" && { echo "FAIL_HINT_LEAK flags='$flags'"; exit 1; } || true
    # 3. Every row has the full Sprint 2 schema.
    echo "$out" | python3 -c "
import sys, json
rows = json.load(sys.stdin)
for r in rows:
    for k in ('session_id','short_id','priority','status','title','project_name','last_activity_at','archived',
              'last_user_prompt','last_assistant_summary','current_task_hint','live'):
        assert k in r, (k, r)
" || { echo "FAIL_SCHEMA flags='$flags'"; exit 1; }
done
echo SCHEMA_OK
```
**Expected:** prints `SCHEMA_OK`.

### Check 24 — Progress fields absent in pre-Sprint-2 record files are tolerated

```bash
python3 -c "
import json, pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/ffffffff-ffff-ffff-ffff-ffffffffffff.json')
p.write_text(json.dumps({
    'session_id': 'ffffffff-ffff-ffff-ffff-ffffffffffff',
    'title': 'legacy', 'priority': 'medium', 'status': 'in_progress',
    'cwd': '/tmp', 'project_name': 'x', 'tags': [], 'note': '',
    'created_at': '2025-01-01T00:00:00Z', 'last_activity_at': '2025-01-01T00:00:00Z',
    'terminal': {'app': None, 'window_id': None, 'tab_id': None, 'tty': None},
    'auto_detected': True, 'archived': False, 'archived_at': None,
}))
"
cst list >/dev/null && echo LEGACY_RECORD_OK || { echo LEGACY_RECORD_FAIL; exit 1; }
cst list --json | python3 -c "
import sys, json
rows = json.load(sys.stdin)
legacy = [r for r in rows if r['session_id'].startswith('ffffffff')][0]
assert legacy['last_user_prompt'] == ''
assert legacy['last_assistant_summary'] == ''
assert legacy['current_task_hint'] == ''
print('LEGACY_JSON_OK')
"
```
**Expected:** prints `LEGACY_RECORD_OK` and `LEGACY_JSON_OK`.

### Check 25 — User cannot write progress fields via `cst set`

```bash
out=$(cst set ffffffff --last-user-prompt 'user-set' 2>&1); rc=$?
[ "$rc" != "0" ] || { echo "FAIL: cst set accepted --last-user-prompt"; exit 1; }
echo NO_USER_WRITE_OK
```
**Expected:** prints `NO_USER_WRITE_OK`. The flag does not exist;
argparse rejects it.

### Check 26 — Missing `name` field in tool_use is tolerated

```bash
python3 -c "
import json, pathlib, os
p = pathlib.Path(os.environ['HOME'], '.claude/projects/-tmp-demo/${SID}.jsonl')
# tool_use with NO name key at all:
p.write_text(json.dumps({'type':'user','message':{'content':'q'},'cwd':'/tmp/demo'}) + '\n'
             + json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','input':{'command':'x'}}]}}) + '\n')
"
cst scan; rc=$?
[ "$rc" = "0" ] || { echo FAIL_SCAN_RC; exit 1; }
python3 -c "
import json, pathlib, os
r = json.loads(pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/${SID}.json').read_text())
assert r['current_task_hint'] == '', r   # no name -> empty hint
print('MISSING_NAME_OK')
"
```
**Expected:** prints `MISSING_NAME_OK`.

### Check 28 — Unicode / emoji / CJK / RTL content round-trips to `cst list`

Addresses amendment §11.8. The contract promises no visual shaping,
only that bytes survive extraction, truncation, and rendering
without `UnicodeEncodeError` or silent stripping.

```bash
python3 -c "
import json, pathlib, os
SID = '66666666-6666-6666-6666-666666666666'
p = pathlib.Path(os.environ['HOME'], f'.claude/projects/-tmp-demo/{SID}.jsonl')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps({'type':'user','message':{'content':'fix 🐛 login 한글 العربية'},'cwd':'/tmp/demo'}) + '\n')
"
cst scan >/dev/null
# UTF-8 stdout even when LANG is C; PYTHONIOENCODING is Python's lever.
out=$(PYTHONIOENCODING=utf-8 cst list)
echo "$out" | grep -qF '🐛'     || { echo FAIL_EMOJI; exit 1; }
echo "$out" | grep -qF '한글'    || { echo FAIL_CJK;   exit 1; }
echo "$out" | grep -qF 'العربية' || { echo FAIL_RTL;   exit 1; }
# Also survives --json:
out_j=$(PYTHONIOENCODING=utf-8 cst list --json)
echo "$out_j" | python3 -c "
import sys, json
rows = json.load(sys.stdin)
hit = [r for r in rows if r['session_id'].startswith('66666666')]
assert hit, rows
lp = hit[0]['last_user_prompt']
assert '🐛' in lp and '한글' in lp and 'العربية' in lp, lp
"
echo UNICODE_DISPLAY_OK
```
**Expected:** prints `UNICODE_DISPLAY_OK`.

### Check 29 — "Fresher wins": scanner does NOT regress a hook-written prompt

Addresses amendment §11.5. The scanner must refrain from overwriting
`last_user_prompt` when the JSONL user line is not newer than the
record's `last_activity_at` (which the hook bumped to "now" when it
wrote the prompt).

```bash
SID=77777777-7777-7777-7777-777777777777
# Create an OLD transcript (mtime in the past) with one user message:
mkdir -p "$HOME/.claude/projects/-tmp-fresh"
JF="$HOME/.claude/projects/-tmp-fresh/${SID}.jsonl"
printf '%s\n' '{"type":"user","message":{"content":"old transcript prompt"},"cwd":"/tmp/fresh"}' > "$JF"
# Force mtime to 1 hour ago:
python3 -c "
import os, time
past = time.time() - 3600
os.utime('$JF', (past, past))
"
cst scan >/dev/null
# Scanner sees the old line and populates the prompt:
got=$(cst list --json | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    if r['session_id'] == '$SID':
        print(r['last_user_prompt']); break
")
[ "$got" = "old transcript prompt" ] || { echo "FAIL_SEED: '$got'"; exit 1; }

# Hook fires later with a NEW prompt → must win and bump last_activity_at.
echo '{"session_id":"'"$SID"'","hook_event_name":"UserPromptSubmit","prompt":"fresh hook prompt"}' \
    | cst hook activity
got2=$(cst list --json | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    if r['session_id'] == '$SID':
        print(r['last_user_prompt']); break
")
[ "$got2" = "fresh hook prompt" ] || { echo "FAIL_HOOK_WRITE: '$got2'"; exit 1; }

# Re-run scan. Transcript is UNCHANGED (same old mtime, same "old transcript prompt"
# line). Scanner must NOT clobber the hook's fresher value.
cst scan >/dev/null
got3=$(cst list --json | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    if r['session_id'] == '$SID':
        print(r['last_user_prompt']); break
")
[ "$got3" = "fresh hook prompt" ] || { echo "FAIL_REGRESSION: scanner clobbered hook prompt -> '$got3'"; exit 1; }

# Opposite direction: touch the JSONL forward and append a genuinely-newer user line;
# scanner should now win because JSONL mtime > last_activity_at.
printf '%s\n' '{"type":"user","message":{"content":"genuinely newer line"},"cwd":"/tmp/fresh"}' >> "$JF"
python3 -c "
import os, time
future = time.time() + 60
os.utime('$JF', (future, future))
"
cst scan >/dev/null
got4=$(cst list --json | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    if r['session_id'] == '$SID':
        print(r['last_user_prompt']); break
")
[ "$got4" = "genuinely newer line" ] || { echo "FAIL_FRESH_WIN: '$got4'"; exit 1; }
echo FRESHER_WINS_OK
```
**Expected:** prints `FRESHER_WINS_OK`.

### Check 30 — Config loader rejects zero / negative / bool values

Addresses amendment §11.11.

```bash
for bad in '{"stale_threshold_seconds": 0}' \
           '{"stale_threshold_seconds": -1}' \
           '{"stale_threshold_seconds": -3600}' \
           '{"stale_threshold_seconds": true}' \
           '{"stale_threshold_seconds": 1.5}' \
           '{"stale_threshold_seconds": "3600"}'; do
    echo "$bad" > "$HOME/.claude/claude-tasks.config.json"
    cst list >/dev/null; rc=$?
    [ "$rc" = "0" ] || { echo "FAIL_RC for '$bad' -> $rc"; exit 1; }
done
# And confirm the threshold still defaults to 14400s: age a session by 90min → NOT stale.
python3 -c "
import json, pathlib, os, datetime
p = pathlib.Path(os.environ['HOME'], '.claude/claude-tasks/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json')
if p.exists():
    r = json.loads(p.read_text())
    r['last_activity_at'] = (datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=90)).strftime('%Y-%m-%dT%H:%M:%SZ')
    r['archived'] = False; r['status'] = 'in_progress'
    p.write_text(json.dumps(r))
"
cst list --stale | grep -q aaaaaaaa && { echo FAIL_WRONGLY_STALE; exit 1; } || true
# Log file mentions the bad-value path.
grep -q 'claude-tasks.config.json' "$HOME/.claude/claude-tasks/.scanner-errors.log" \
    || { echo FAIL_NO_LOG; exit 1; }
echo CONFIG_NEGATIVE_OK
```
**Expected:** prints `CONFIG_NEGATIVE_OK`.

### Check 27 — Sprint 1 regression via JSON surface

(Sprint 1's stdout TSV gains a leading live-dot column; its one-off
check script must be consulted via the JSON surface instead.)

```bash
# Full Sprint 1 pytest suite must still pass unchanged:
python3 -m pytest -q tests/test_registry.py tests/test_scanner.py \
                      tests/test_hooks.py tests/test_cli.py tests/test_installer.py
```
**Expected:** all Sprint 1 tests remain green. Any Sprint 1 test
that depended on exact TSV column count is migrated to parse
`cst list --json` and is explicitly listed in §6 as "extended".

## 6. Test harness

Sprint 1 had 43 passing tests across five files. Sprint 2 extends
those files and adds seven new ones. All tests run under the same
`tests/conftest.py` HOME-redirection guard.

Augmented files:

- `tests/test_registry.py` — add:
  - `test_new_record_seeds_progress_fields_empty`
  - `test_read_tolerates_missing_progress_fields`
  - `test_update_refuses_progress_field_keys` (registry.update refuses
    `last_user_prompt`, `last_assistant_summary`, `current_task_hint`
    — defense in depth even though the CLI has no flag).

- `tests/test_scanner.py` — add:
  - `test_extracts_last_user_prompt`
  - `test_extracts_last_assistant_summary_first_line`
  - `test_extracts_last_assistant_summary_skips_tool_use_parts`
  - `test_extracts_current_task_hint_bash`
  - `test_extracts_current_task_hint_edit_write_multiedit_notebookedit` (parametrized)
  - `test_extracts_current_task_hint_read`
  - `test_extracts_current_task_hint_grep_glob` (parametrized)
  - `test_current_task_hint_empty_when_no_tool_use_in_tail`
  - `test_current_task_hint_fallback_to_bare_tool_name_when_missing_input`
  - `test_current_task_hint_empty_when_tool_use_has_no_name`
  - `test_current_task_hint_relpath_when_file_under_cwd`
  - `test_current_task_hint_basename_when_file_outside_cwd`
  - `test_truncation_100_chars_with_single_ellipsis`
  - `test_truncation_is_code_point_aware_for_cjk`
  - `test_non_utf8_bytes_do_not_crash`
  - `test_scanner_always_overwrites_progress_even_when_auto_detected_false`
  - `test_scanner_error_isolated_to_single_transcript` (one malformed
    file alongside one good; asserts good record gets progress, bad
    one's pre-existing progress is preserved, and the scanner error
    log has exactly one entry).
  - `test_scanner_limits_tail_window_to_50_lines` — seeds a 1000-line
    transcript; asserts the hint/prompt/summary come from the last
    50 lines and earlier lines don't leak in. Additionally, a
    boundary case asserts the window is EXACTLY the last 50: line 50
    from the end is visible, line 51 from the end is not.
  - `test_scanner_does_not_regress_hook_written_prompt` — pre-seed a
    record with `last_user_prompt='hook-text'` and
    `last_activity_at=now`; set the JSONL mtime to 1 hour ago with a
    user message `old-text`; run the scanner; assert
    `last_user_prompt == 'hook-text'` (unchanged).
  - `test_scanner_overwrites_prompt_when_jsonl_is_newer` — inverse:
    JSONL mtime is strictly newer than `last_activity_at`, JSONL
    user line differs from stored; assert scanner overwrites.
  - `test_scanner_does_not_overwrite_when_extracted_value_equals_stored`
    (no-op writes are avoided; the file's mtime should not bump when
    nothing changed — asserted via `os.stat(record_path).st_mtime`
    pre/post).

- `tests/test_hooks.py` — add:
  - `test_activity_hook_writes_last_user_prompt_from_stdin`
  - `test_activity_hook_accepts_user_prompt_key_variant` (payload
    uses `user_prompt` instead of `prompt`).
  - `test_activity_hook_truncates_long_prompt_from_stdin`
  - `test_activity_hook_ignores_missing_prompt_field` (writes
    nothing to `last_user_prompt`; still bumps `last_activity_at`).
  - `test_activity_hook_does_not_write_assistant_summary_or_task_hint`
  - `test_activity_hook_on_unknown_session_id` — covers the
    create-skeleton branch from §2.3: hook invoked with a
    `session_id` that has no record on disk creates one with the
    defaults; `last_user_prompt` populated when payload supplied it.
    Exit 0.

- `tests/test_cli.py` — add:
  - `test_list_multiline_shows_arrow_and_gear`
  - `test_list_compact_strips_subrows_one_line_per_session`
  - `test_list_json_includes_new_progress_fields_and_live`
  - `test_set_rejects_progress_field_flags` (argparse rejects the
    unknown flags; exit != 0).
  - `test_list_stale_banner_appears`
  - `test_list_stale_flag_filters`
  - `test_list_stale_label_overrides_displayed_status_but_not_stored`
  - `test_prefix_exact_full_uuid`
  - `test_prefix_min_six_chars_exact_match`
  - `test_prefix_ambiguous_exits_3_and_does_not_mutate` (SHA256
    before/after).
  - `test_prefix_too_short_exits_2`
  - `test_prefix_not_found_exits_1`
  - `test_prefix_applies_to_done_archive_focus_resume` (parametrized
    over subcommands).

- `tests/test_installer.py` — add:
  - `test_install_sets_fresh_statusline` — asserts the FULL
    `statusLine` object shape: keys exactly
    `{"type","command","padding"}`, `type == "command"`,
    `command == "cst statusline"`, `padding == 0`
    (amendment §11.9).
  - `test_install_preserves_custom_statusline` — retained from
    Sprint 1; extend to assert a warning line on stdout mentioning
    `existing statusline`.
  - `test_install_idempotent_statusline` — second run leaves the
    matching `cst statusline` entry untouched AND preserves the
    full object shape.

New files:

- `tests/test_focus.py`
  - `test_focus_iterm_runs_expected_osascript_args`
  - `test_focus_iterm_osascript_string_matches_template` — exact
    match (or parameterised regex with only `<W>` varying) against
    the iTerm2 template pinned in §2.8.
  - `test_focus_terminal_app_runs_expected_osascript_args` (with
    window_id only, with window_id+tab_id, and with neither).
  - `test_focus_terminal_app_osascript_string_matches_template` —
    same byte-level pin for the Terminal.app templates.
  - `test_focus_iterm_no_window_id_falls_back_to_activate`
  - `test_focus_unsupported_app_exits_4_with_resume_hint`
  - `test_focus_null_app_treated_as_unsupported`
  - `test_focus_osascript_failure_exits_5_with_resume_hint`
  - `test_focus_non_macos_exits_6` (via `CST_FORCE_PLATFORM=linux`).
  - `test_focus_ambiguous_prefix_exits_3_before_platform_guard` —
    on Linux with an ambiguous prefix: exit 3, not 6.
  - `test_focus_window_id_is_integer_only` — a record whose
    `terminal.window_id` is a string / float / dict yields exit 5
    with the corrupt-window-id message (amendment §11.2); no
    AppleScript is ever built with a non-int.

- `tests/test_resume.py`
  - `test_resume_builds_cd_and_claude_resume_command`
  - `test_resume_iterm_osascript_string_matches_template` — byte-
    pin against the iTerm2 resume template (§2.8).
  - `test_resume_terminal_app_osascript_string_matches_template`.
  - `test_resume_no_cwd_exits_1`
  - `test_resume_no_supported_terminal_exits_4`
  - `test_resume_non_macos_exits_6`
  - `test_resume_shell_escapes_cwd` — parameterized over cwds:
    `"/tmp/a b"`, `"/tmp/a'b"`, `'/tmp/a"b'`, `"/tmp/a$(x)b"`,
    `"/tmp/a;b"`. Each must produce a SHELL layer where the
    dangerous character is inside `shlex.quote`'d single-quoted
    form and cannot break out of the string.
  - `test_resume_applescript_escapes_backslash_and_quote` — cwd
    containing `\` and `"` must be escaped per §2.8 rule 2 before
    being wrapped in the AppleScript string literal.
  - `test_resume_rejects_cwd_with_newline_or_null` — ValueError
    surfaced as exit 1 with a clear message.
  - `test_resume_ambiguous_prefix_exits_3_before_platform_guard`.

- `tests/test_live_dot.py`
  - `test_live_dot_marks_matching_tty`
  - `test_live_dot_default_when_no_tty_stored`
  - `test_live_dot_degrades_silently_when_ps_fails`
  - `test_live_dot_ignores_non_claude_processes` — includes a
    `node /path/claude-cli.js` row (basename = `node`) that must
    NOT be treated as a claude process.
  - `test_live_dot_comm_parsing_with_spaces` — fake `ps` output
    contains a row whose `comm` column is
    `/Applications/Claude Helper/claude` (space-containing path).
    Asserts the parser's `split(None, 2)` keeps the full path in
    col 3, basename resolves to `claude`, and the row matches.
  - `test_live_dot_skips_rows_with_question_tty` — `??` tty rows
    are never matched.
  - `test_live_dot_skips_malformed_pid` — a header-like row is
    silently skipped, not crashed on.

- `tests/test_gc.py`
  - `test_gc_deletes_only_old_archived`
  - `test_gc_never_deletes_non_archived`
  - `test_gc_uses_archived_at_not_mtime` (file mtime is recent, but
    `archived_at` is old: must delete).
  - `test_gc_tolerates_unparseable_archived_at` (skips record, logs
    warning, exits 0; file still present).
  - `test_gc_partial_failure_continues_and_exits_1`

- `tests/test_review_stale.py`
  - `test_review_stale_keep_is_byte_identical`
  - `test_review_stale_skip_is_byte_identical`
  - `test_review_stale_done_flips_status`
  - `test_review_stale_archive_sets_archived_at`
  - `test_review_stale_empty_exits_0_with_message`
  - `test_review_stale_non_interactive_treats_remaining_as_skip`
  - `test_review_stale_unrecognized_input_reprompts_then_skips`
  - `test_review_stale_presents_three_sessions_in_priority_order` —
    seeds A(high), B(medium), C(low) all stale; feeds three "skip"
    lines; asserts the three short_id prefixes appear in stdout in
    that exact order and all three records are byte-identical
    afterward (addresses amendment §11.12).

- `tests/test_statusline.py`
  - `test_statusline_empty_registry_prints_empty_line`
  - `test_statusline_empty_registry_omits_arrow_and_tasks_nudge` —
    explicit: the `→  /tasks` suffix is forbidden when pending == 0.
  - `test_statusline_pending_only_shape`
  - `test_statusline_pending_and_stale_shape`
  - `test_statusline_omits_stale_segment_when_zero`
  - `test_statusline_200_records_under_150ms` — wall-time via
    `time.perf_counter`; 3 runs, min < 0.15s.
  - `test_statusline_does_not_invoke_subprocess` — monkeypatches
    `subprocess.run` to raise; statusline must still succeed.

- `tests/test_config.py`
  - `test_config_missing_uses_default_4h`
  - `test_config_overrides_threshold`
  - `test_config_malformed_falls_back_to_default_and_logs`
  - `test_config_bad_value_type_falls_back_to_default_and_logs` —
    types covered: `str`, `float`, `list`, `dict`, `None`, `bool`
    (bool is explicitly NOT treated as an int per §2.10).
  - `test_config_zero_or_negative_falls_back_to_default` —
    parametrized over `0`, `-1`, `-3600` (addresses amendment
    §11.11).
  - `test_env_var_overrides_config_file`

Expected total: Sprint 1 (43) + ~95 new tests ≈ 135+ tests.

Run:

```bash
python3 -m pytest -q tests/
```

All tests MUST pass before handoff. Sprint 1's 43 must remain green;
any Sprint 1 test whose on-disk record shape assumption changes (only
the progress fields get added) is updated in-place and still counts
toward that 43.

## 7. Stack / tooling decisions

- **Python 3.11+** (unchanged from Sprint 1).
- **Stdlib-only where possible**. The one new import surface is
  `subprocess` for:
  - `ps -o pid,tty,comm -A` (live dot).
  - `osascript` invocation (focus/resume).
  Tests never invoke real `osascript`; production code does.
- **No `rich` in Sprint 2**. Multi-line `cst list` uses plain ANSI
  dim escapes (`\x1b[2m…\x1b[0m`) only when `sys.stdout.isatty()` is
  true. On non-TTY stdout (pipes, CI, `cst list > file`), the
  escapes are omitted — the `⤷` / `⚙` markers remain. `rich` lands
  in Sprint 3 with the `watch` TUI.
- **AppleScript is inlined**. No `.scpt` files. The osascript
  commands are Python string literals inside `focus.py` / `resume.py`.
- **No new third-party deps**. `pyyaml` still not needed.
- **Platform detection**: single helper `platform_macos.is_macos()`
  that honors the `CST_FORCE_PLATFORM` test override. All
  macOS-specific code paths route through it.
- **Installer** remains bash + `python3 -m` into
  `scripts/installer.py`. Statusline merge adds one more
  subcommand to `installer.py`: `merge-statusline`.
- **Version bump**: `cst --version` prints `cst 0.2.0`.
- **Error log files** (both non-fatal, append-only, in
  `~/.claude/claude-tasks/`):
  - `.hook-errors.log` — already exists from Sprint 1.
  - `.scanner-errors.log` — NEW. Used by scanner progress extraction,
    config loader, and gc's unparseable-timestamp path.

## 8. File layout after Sprint 2

```
claude-session-manager/
├── spec.md
├── _brainstorm-design.md
├── sprint_1_contract.md                 # archived from Sprint 1
├── sprint_contract.md                   # THIS file (Sprint 2)
├── generator_report.md                  # rewritten at Sprint 2 handoff
├── SKILL.md                             # updated: version, new commands, progress fields
├── install.sh                           # + statusline wiring
├── scripts/
│   ├── cst.py                           # + list multi-line + --compact,
│   │                                    #   + statusline/focus/resume/gc/review-stale
│   │                                    #   + prefix resolver, macOS guard, new --json keys
│   ├── registry.py                      # + progress field defaults + update refusal
│   ├── scanner.py                       # + _extract_progress, tool-hint builders,
│   │                                    #   + scanner error log, 50-line tail window
│   ├── hooks.py                         # + last_user_prompt from UserPromptSubmit payload
│   ├── focus.py                         # NEW: iTerm2/Terminal.app osascript + fallbacks
│   ├── resume.py                        # NEW: new-window-with-cd-and-resume
│   ├── statusline.py                    # NEW: fast read-only count + format
│   ├── livedot.py                       # NEW: ps-based tty matching (stdlib subprocess)
│   ├── gc.py                            # NEW: archived_at-based deletion
│   ├── review_stale.py                  # NEW: interactive keep/done/archive/skip
│   ├── config.py                        # NEW: stale_threshold_seconds loader
│   ├── platform_macos.py                # NEW: is_macos() with CST_FORCE_PLATFORM hook
│   └── installer.py                     # + merge-statusline subcommand
└── tests/
    ├── conftest.py                      # unchanged
    ├── test_registry.py      (extended)
    ├── test_scanner.py       (extended)
    ├── test_hooks.py         (extended)
    ├── test_cli.py           (extended)
    ├── test_installer.py     (extended)
    ├── test_focus.py         (NEW)
    ├── test_resume.py        (NEW)
    ├── test_live_dot.py      (NEW)
    ├── test_gc.py            (NEW)
    ├── test_review_stale.py  (NEW)
    ├── test_statusline.py    (NEW)
    └── test_config.py        (NEW)
```

No `references/` required; SKILL.md remains < 500 lines. No
`assets/`. No `commands/` (slash commands are Sprint 3).

## 9. Non-goals for this sprint

Slash commands, `cst watch` TUI, `cst watch --pin` — all deferred to
Sprint 3. After Sprint 2 the only remaining items from the original
Sprint 1 deferred list are these three.

Also explicitly out of scope for Sprint 2:

- Editing progress fields from any user-facing surface.
- Any AI inference for progress fields (the spec §7 bans this).
- Tracking per-window bindings beyond what Sprint 1 captures —
  terminal app/window/tab/tty are already stored and `cst focus`
  uses them as-is; no new capture mechanism is added.
- A dedicated "statusline refresh" hook — statusline is on-demand
  via Claude Code's existing `statusLine.command`.

## 10. Risks and assumptions

Each risk below includes a concrete mitigation that is testable in
this sprint, not a hand-wave.

1. **Claude Code UserPromptSubmit payload shape.** We assume the
   payload delivers a `prompt` field (string) containing the
   submitted text. If the actual field name is `user_prompt` or
   nested under `message`, the hook handler reads BOTH top-level
   variants and uses the first that is a non-empty string. If
   neither is present, the hook still exits 0 and logs once.
   **Mitigation (testable):** `test_activity_hook_accepts_user_prompt_key_variant`
   covers the alternate name; the scanner is an independent path to
   the same fact — worst case the prompt lands with ~2-second lag.

2. **Claude Code transcript JSONL stability.** We depend on:
   `type == "user"` / `"assistant"`, `message.content` either a
   string or a list of `{type: "text"|"tool_use", ...}` parts,
   `tool_use.name` and `tool_use.input`. If a future Claude Code
   release changes the shape, the scanner falls back to empty
   progress fields (isolated error log) and lists still work.
   **Mitigation (testable):**
   `test_scanner_error_isolated_to_single_transcript` and
   `test_current_task_hint_empty_when_tool_use_has_no_name` prove
   the fallbacks don't cascade.

3. **Performance of `ps -A` on busy machines.** Historical `ps` can
   be slow in exotic environments. We cache the ps output for the
   duration of a single `cst list` invocation and never run it from
   the statusline path. **Mitigation (testable):**
   `test_statusline_does_not_invoke_subprocess` monkeypatches
   `subprocess.run` to raise and asserts statusline still succeeds;
   `test_statusline_200_records_under_150ms` asserts the perf
   budget.

4. **AppleScript + `window_id` authenticity.** Sprint 1 does not
   populate `terminal.window_id` in any reliable way. `cst focus`
   therefore degrades to "activate the app" in many real-world
   cases. **Mitigation (testable):**
   `test_focus_iterm_no_window_id_falls_back_to_activate` asserts
   the graceful degrade; `cst resume` is the always-working
   fallback, and every focus failure path suggests it (see
   `test_focus_osascript_failure_exits_5_with_resume_hint`).

5. **ISO-8601 timezone handling for `archived_at`.** Sprint 1 stored
   timestamps with a `Z` suffix (UTC). `cst gc` parses those. If a
   record somehow got a tz-naive or non-Z timestamp (hand-edit),
   `gc` logs + skips + exits 0 rather than deleting.
   **Mitigation (testable):**
   `test_gc_tolerates_unparseable_archived_at`.

6. **Config file race.** A user writing the config while `cst list`
   is reading it is atypical. We read, parse, and ignore errors — no
   lock, no retry. **Mitigation:** the malformed-JSON test and
   Check 20 demonstrate `cst list` never fails because of config.

7. **Stale threshold env var** (`CST_STALE_THRESHOLD_SECONDS`) is
   documented internally as tests-only but enforced only at the
   loader. A user setting it in their shell works and is harmless;
   we do not advertise it in SKILL.md.

8. **Pre-Sprint-2 records on disk.** Existing records without the
   three progress-field keys are read as if those keys were `""`.
   They become persisted only after the next scan or `cst set`.
   **Mitigation (testable):**
   `test_read_tolerates_missing_progress_fields` + Check 24.

9. **Glyph availability.** `⤷` (U+2937) and `⚙` (U+2699) are
   load-bearing for Sprint 2's display. Terminals that can't render
   them show `?` — acceptable; no further guarantee is made. Tests
   use literal code points and pass regardless of terminal
   rendering.

10. **Sprint 1 Check 7 script compatibility.** Adding a leading
    live-dot column to `cst list` stdout breaks the one-off
    `/tmp/run_checks.sh` from Sprint 1. Check 27 re-verifies Sprint 1
    contracts via the pytest suite instead (forward-compatible).
    **Mitigation (testable):** Sprint 1 pytest tests MUST pass
    unchanged in Check 27; any that had TSV-ordering assumptions are
    updated to use `cst list --json` and remain part of the Sprint 1
    test count.

11. **"Fresher wins" for `last_user_prompt`.** Resolved in §2.2:
    the scanner only overwrites `last_user_prompt` when JSONL mtime
    is strictly newer than the record's `last_activity_at` AND the
    extracted value is non-empty AND differs from the stored value.
    The hook bumps `last_activity_at` on write, protecting its
    value until Claude Code flushes a genuinely newer transcript
    line. **Mitigation (testable):**
    `test_scanner_does_not_regress_hook_written_prompt`,
    `test_scanner_overwrites_prompt_when_jsonl_is_newer`, and
    Check 29.

12. **`ps` output format drift.** macOS `ps -o pid,tty,comm -A`
    uses `??` for processes with no controlling tty. Our tty matcher
    rejects that sentinel. **Mitigation (testable):**
    `test_live_dot_ignores_non_claude_processes`,
    `test_live_dot_skips_rows_with_question_tty`, and
    `test_live_dot_comm_parsing_with_spaces` cover the parsing
    surface per §2.5.

13. **AppleScript injection via `window_id` / `cwd` / `session_id`.**
    A hand-edited or hostile record could carry a `window_id` that
    is a string like `1) activate end tell -- …` attempting to
    break out of the AppleScript. §2.8 pins three escape layers:
    shell-layer `shlex.quote`, AppleScript-layer `"`/`\`
    escaping, and `window_id`/`tab_id` forced through `int()`
    before interpolation. **Mitigation (testable):**
    `test_focus_window_id_is_integer_only`,
    `test_resume_shell_escapes_cwd`,
    `test_resume_applescript_escapes_backslash_and_quote`,
    `test_resume_rejects_cwd_with_newline_or_null`.

14. **Hook invoked for unknown `session_id`.** §2.3 picks the
    create-skeleton branch: the hook writes a minimal record so
    the session shows up in `cst list` without waiting for the
    scanner. **Mitigation (testable):**
    `test_activity_hook_on_unknown_session_id`.

## 11. Evaluator amendments (binding)

**Verdict: AMEND**

The contract is substantially stronger than a typical first draft — the scanner-owns-progress rule is unambiguous, truncation has real code-point tests, focus/resume have exit-code discipline, and Check 27 protects the Sprint 1 surface. But several load-bearing behaviors are under-specified or under-tested, and three of the adversarial probes the user explicitly called out are not observable-checked. Amend before coding.

### Required amendments

1. **Pin the exact osascript strings for `cst focus` and `cst resume`** (user-directed focus area 3 and 4; spec §8 requires the iTerm2 and Terminal.app DoD bullets work for real). §2.8 says "unit-tested via captured args" but the contract nowhere specifies what those args are. Without a pinned template the Generator could ship any script that passes the mock. Add to §2.8 the literal osascript snippets (or an unambiguous Python f-string template) for each of:
   - iTerm2 focus with `window_id`: e.g. `tell application "iTerm2" to tell window id <W> to select`. Specify the activation step too.
   - iTerm2 focus without `window_id`: `tell application "iTerm2" to activate`.
   - Terminal.app focus with `window_id` + optional `tab_id`.
   - iTerm2 resume (new window running `cd <cwd> && claude --resume <session_id>`).
   - Terminal.app resume fallback.
   Add `test_focus_iterm_osascript_string_matches_template` (regex-match the captured `_run_osascript` args) so a drift in the template breaks a test.

2. **`cst focus`/`cst resume` must shell-escape the id, cwd, and window_id** (adversarial: a session id or cwd containing `"` breaks an unescaped AppleScript). The contract has `test_resume_shell_escapes_cwd` but no matching escape test for `focus`, and the contract does not specify HOW escaping is done (AppleScript string literal escaping is different from POSIX shell escaping; the `cd` inside the AppleScript needs shell-level quoting). Pin the escaping helper (e.g. `shlex.quote` for the shell portion, doubled `"` for AppleScript strings) in §2.8 and add `test_focus_window_id_is_integer_only` (refuse non-int `window_id` to close AppleScript injection).

3. **Add an observable check for ambiguous prefix blocking every mutating subcommand** (spec §8: *"An ambiguous prefix lists candidate sessions and exits non-zero without mutating anything"* — "any command that takes a session id", not just `set`). Current Check 12 only exercises `cst set`. Add Check 12b that runs `cst done aabbccdd`, `cst archive aabbccdd`, `cst focus aabbccdd`, and `cst resume aabbccdd` against the same two-record ambiguous fixture and asserts rc==3, "ambiguous" in stderr, and SHA256 of both record files byte-identical after each invocation. (`cst focus`/`cst resume` should still exit 3 on ambiguity BEFORE the macOS-guard / terminal-lookup path — specify this ordering in §2.8 and §2.7.)

4. **Add an observable check for `cst gc` on an empty / zero-archived registry** (user-directed adversarial probe). Today only `test_gc_deletes_only_old_archived` touches this path and it's a pytest test with fixture records. Add an observable Check 21b:
   ```bash
   rm -rf "$HOME/.claude/claude-tasks"; mkdir -p "$HOME/.claude/claude-tasks"
   out=$(cst gc); rc=$?
   [ "$rc" = "0" ] || { echo FAIL_RC; exit 1; }
   echo "$out" | grep -qE 'deleted 0' || { echo FAIL_MSG; exit 1; }
   # And with only NON-archived records present:
   CLAUDE_SESSION_ID=dddddddd-dddd-dddd-dddd-dddddddddddd CLAUDE_PROJECT_DIR=/tmp/g cst hook session-start < /dev/null
   before=$(shasum -a 256 "$HOME/.claude/claude-tasks/dddddddd-"*.json)
   cst gc; rc=$?
   after=$(shasum -a 256 "$HOME/.claude/claude-tasks/dddddddd-"*.json)
   [ "$rc" = "0" ] && [ "$before" = "$after" ] || { echo FAIL_NOARCH_MUTATED; exit 1; }
   echo GC_EMPTY_OK
   ```

5. **Specify the "fresher wins" rule concretely** (spec §5: *"Also refreshed from Claude Code's user-prompt-submit signal; whichever signal is fresher wins"*). §2.3 and risk §11 punt: hook writes, then the next scanner pass unconditionally overwrites with whatever JSONL currently has, even if the hook's value was strictly newer (the hook fires before Claude Code flushes the new user line to JSONL). Either:
   - (a) Add a per-record `last_user_prompt_source_at` timestamp and have the scanner only overwrite `last_user_prompt` when its source (JSONL mtime or the most recent user line's timestamp, whichever is applicable) is strictly newer than the stored `last_user_prompt_source_at`; OR
   - (b) Have the scanner ONLY overwrite `last_user_prompt` when the extracted value is non-empty AND different from the stored one AND the JSONL mtime is newer than the stored `last_activity_at`. (Simpler — pick this.)
   Document the chosen rule in §2.2 and add `test_scanner_does_not_regress_hook_written_prompt` — pre-seed a record with a hook-written prompt, then run the scanner against a JSONL whose user message is OLDER than the hook write: scanner must NOT clobber the hook's prompt.

6. **`--json` must omit the stale banner AND support `--all` / `--stale` in combination** (§2.4: "Under `--json` the footer is omitted"). Add an observable assertion:
   ```bash
   cst list --json | python3 -c "import sys,json; json.load(sys.stdin)"   # must parse cleanly
   cst list --stale --json | python3 -c "..."   # must parse cleanly
   cst list --all --json   | python3 -c "..."
   ```
   and assert that none of these outputs contain the literal `⚠` or `run 'cst review-stale'`. Current Check 23 only exercises default `cst list --json`.

7. **`cst hook activity` behavior when no prior record exists for the session_id** (edge case not in §2.3). Today the hook "bumps `last_activity_at`" — implying an in-place update. If the record doesn't yet exist (hook fires before session-start hook on a brand-new session, or in the test sandbox) should it:
   - (a) Create a skeleton record with just `session_id` + `last_activity_at` + `last_user_prompt`? OR
   - (b) Log one warning and exit 0 without writing?
   Pick and document in §2.3; add `test_activity_hook_on_unknown_session_id` covering the chosen branch.

8. **Add an observable check for progress-field display under unicode / emoji content** (user-directed probe "unicode prompt with emojis/RTL displayed correctly"). CJK truncation is tested but the `cst list` multi-line RENDER path with emojis is not. Add:
   ```bash
   python3 -c "
   import json, pathlib, os
   p = pathlib.Path(os.environ['HOME'], '.claude/projects/-tmp-demo/${SID}.jsonl')
   p.write_text(json.dumps({'type':'user','message':{'content':'fix 🐛 login 한글 العربية'},'cwd':'/tmp/demo'}) + '\n')
   "
   cst scan >/dev/null
   out=$(cst list)
   echo "$out" | grep -qF '🐛' || { echo FAIL_EMOJI; exit 1; }
   echo "$out" | grep -qF '한글'  || { echo FAIL_CJK;   exit 1; }
   echo "$out" | grep -qF 'العربية' || { echo FAIL_RTL; exit 1; }
   echo UNICODE_DISPLAY_OK
   ```
   The contract need not guarantee visual RTL shaping — only that the bytes round-trip to stdout without `UnicodeEncodeError` and without stripping.

9. **Statusline installer must assert the full wired object, not just the `command` string** (§2.9: *"set `"statusLine": {"type": "command", "command": "cst statusline", "padding": 0}`"*). Check 18's `FRESH_SET` asserts only `command == 'cst statusline'`. Extend to assert `type == 'command'` and `padding == 0`; otherwise a Generator that writes just `{"command": "cst statusline"}` passes the check but produces a non-working `settings.json` for Claude Code.

10. **Define "`comm` basename matches exactly `claude`" precisely** (§2.5). On macOS, `ps -o comm` returns the full executable path truncated to a column width; `comm` basename for `/Applications/Claude.app/.../claude` is `claude`, but for `node /path/claude-cli.js` it's `node`. The contract says "exactly `claude`" — good — but does not specify column parsing robustness (tty column is whitespace-separated and `comm` can contain spaces on macOS when truncated). Pin the parsing: "split the line on whitespace; PID in col 1, TTY in col 2, and basename of `comm` is `os.path.basename(rest_of_line)`". Add `test_live_dot_comm_parsing_with_spaces` fixture.

11. **Config loader must reject negative / zero `stale_threshold_seconds`** (§2.10 says "not a positive int → default"). Current test is `test_config_bad_value_type_falls_back_to_default_and_logs` — this covers type but not value range. Add `test_config_zero_or_negative_falls_back_to_default` with explicit `0` and `-1` cases.

12. **`cst review-stale` must reject partial matches / be order-stable** (spec §3 Flow D: "one at a time" — user should not see the same session re-presented or skipped sessions silently promoted). The contract says "priority then recency" ordering but never pins it in a check. Add to Check 11:
    - Seed two stale sessions (A with priority high, B with priority medium).
    - `printf 'skip\nkeep\n' | cst review-stale` must present A before B (assert by reading stdout prompts with short_id prefix).
    - After the run, neither record's SHA256 has changed.
    Currently Check 11 only uses one session.

### Out-of-scope polish notes (non-binding)

- Risk §10 is honest about `sprint_1_contract.md` not being here — good; the cross-reference is fine.
- The `→  /tasks` suffix in statusline is load-bearing per spec §6.3 but the Generator could elide the nudge arrow under the zero-pending branch (it already does). Consider adding a check that the arrow is NEVER present when `pending == 0`.
- `test_scanner_limits_tail_window_to_50_lines` is a great boundary test; consider asserting the exact last-50-line window (not last-49 or last-51).
- Risk §9: "terminals that can't render them show `?`" — consider a `CST_ASCII_MARKERS=1` env to swap `⤷`/`⚙` for ASCII `>` / `!` in pathological terminals. Not required for Sprint 2.
- `cst gc` summary message: pin the exact format in a check (currently Check 21 only asserts file existence). Minor.

CONTRACT_REVIEW_READY: sprint_contract.md

## 12. Evaluator re-review

**Verdict: APPROVE**

All 12 binding amendments from §11 are substantively satisfied. The contract now pins AppleScript templates as literal multi-line text with distinct shell+AppleScript escape layers, implements a concrete "fresher wins" rule that tests both directions, and adds the missing observable checks (12b, 21b, 28, 29, 30). Per-amendment status:

- #1 ✓ satisfied — §2.8 pins five focus templates + two resume templates as literal multi-line AppleScript with `<W>` / `<T>` / `<SHELL_CMD>` slot markers. `test_resume_iterm_osascript_string_matches_template` and `test_resume_terminal_app_osascript_string_matches_template` do byte-pin assertions. **Minor note**: the focus side has no parallel `test_focus_iterm_osascript_string_matches_template` in the enumerated test harness; §2.8's binding text ("Tests assert byte-for-byte equality ... Drift in the template BREAKS a test") means the Generator still owes this — added below as a non-blocking nit.
- #2 ✓ satisfied — §2.8 "Escaping / injection hardening" pins two distinct quoting layers: `shlex.quote` for the POSIX shell string, `\\` + `\"` for the AppleScript string literal; integer hardening via `int()` + `f"{n:d}"`. `session_id` validated `^[0-9a-f-]{36}$`; cwd validated for newline/null. Tests: `test_focus_window_id_is_integer_only`, `test_resume_applescript_escapes_backslash_and_quote`, `test_resume_rejects_cwd_with_newline_or_null`, parametrized `test_resume_shell_escapes_cwd` over five dangerous characters.
- #3 ✓ satisfied — Check 12b loops over `set`/`done`/`archive`/`focus`/`resume` with the two-record ambiguous fixture AND `CST_FORCE_PLATFORM=linux` (forcing focus/resume down the non-macOS path so the test can only pass if the resolver fires first). Asserts exit 3, "ambiguous" in stderr, both records byte-identical. §2.8 pins the resolver-before-platform-guard ordering. `test_focus_ambiguous_prefix_exits_3_before_platform_guard` + `test_resume_ambiguous_prefix_exits_3_before_platform_guard` mirror.
- #4 ✓ satisfied — Check 21b covers (a) zero-record registry and (b) all-non-archived registry. Asserts exit 0, byte-identical SHA256 on the non-archived record, exact summary string `cst gc: deleted 0 record(s); kept 0 archived record(s) still within the 7-day window`. Also closes the polish note about pinning the gc summary format (now enforced in both Check 21 and 21b via regex).
- #5 ✓ satisfied — §2.2 rule 3 adopts option (b) unambiguously: scanner overwrites only when extracted value is non-empty AND differs from stored AND JSONL mtime > stored `last_activity_at`. Check 29 tests BOTH directions: seeds scanner-written prompt → hook writes newer prompt → re-scan against the now-older transcript does NOT clobber (`FAIL_REGRESSION`); then appends a newer user line, bumps JSONL mtime to the future, re-scans → scanner correctly overwrites (`FAIL_FRESH_WIN`). Test harness adds `test_scanner_does_not_regress_hook_written_prompt`, `test_scanner_overwrites_prompt_when_jsonl_is_newer`, and `test_scanner_does_not_overwrite_when_extracted_value_equals_stored`.
- #6 ✓ satisfied — Check 23 loops over `""`, `--all`, `--stale`, `--all --stale`; asserts JSON parses, no `⚠` or `run 'cst review-stale'` chrome leaks, and full Sprint 2 schema present on every row.
- #7 ✓ satisfied — §2.3 picks option (a) explicitly: activity hook on an unknown `session_id` creates a skeleton via `new_record(sid)` with `last_activity_at=now`, optional truncated `last_user_prompt`, and null terminal/cwd. `test_activity_hook_on_unknown_session_id` exercises the branch.
- #8 ✓ satisfied — Check 28 round-trips 🐛 (emoji), 한글 (CJK), and العربية (RTL) through `cst list` AND `cst list --json`. `PYTHONIOENCODING=utf-8` is pinned to make the check robust to `LANG=C` CI environments.
- #9 ✓ satisfied — Check 18 asserts the full statusLine object (`type == 'command'`, `command == 'cst statusline'`, `padding == 0`) AND `set(sl.keys()) == {'type','command','padding'}` — rejecting both missing keys and stray extra keys, in both fresh-install and idempotent-rerun phases.
- #10 ✓ satisfied — §2.5 pins parsing: `line.split(None, 2)`, PID regex `^[0-9]+$`, tty sentinels (`?`/`??`/`-`) skipped, `ttysN`/`ttyN`/`pts/N` prepended with `/dev/`, basename of col 3 compared to `"claude"`. Tests: `test_live_dot_comm_parsing_with_spaces` (`/Applications/Claude Helper/claude`), `test_live_dot_ignores_non_claude_processes` (includes `node /path/claude-cli.js`), `test_live_dot_skips_rows_with_question_tty`, `test_live_dot_skips_malformed_pid`.
- #11 ✓ satisfied — §2.10 binds `type(v) is int` (rejecting `bool`) AND strictly positive (rejecting `0` and negatives); each bad path logs one warning to `.scanner-errors.log`. Check 30 parametrizes over `0`, `-1`, `-3600`, `true`, `1.5`, `"3600"`; asserts exit 0, default behavior preserved (90-min-old session is NOT flagged stale), and log entry present. `test_config_zero_or_negative_falls_back_to_default` + `test_config_bad_value_type_falls_back_to_default_and_logs` (now including `bool`) mirror.
- #12 ✓ satisfied — Check 11 Phase B seeds three stale records (A=high, B=medium, C=low), feeds three `skip` lines, asserts line-number ordering via `grep -n` + `cut` (A before B before C), all three records byte-identical afterward, and short-read EOF fallback exits 0. `test_review_stale_presents_three_sessions_in_priority_order` mirrors.

### Non-blocking nits for the Generator to address while coding

- Test harness for `tests/test_focus.py` is missing a template-match test analogous to `test_resume_iterm_osascript_string_matches_template`. §2.8's binding clause already promises "byte-for-byte equality ... drift BREAKS a test"; please add `test_focus_iterm_osascript_string_matches_template` and `test_focus_terminal_app_osascript_string_matches_template` (with both `window_id` present and `window_id=null` variants) so the focus templates are enforceable.
- Check 29's "future mtime" step (`future = time.time() + 60`) is pragmatic, but some filesystems round mtime to the nearest second and some CI schedulers reject future-dated files; if the check proves flaky, use a stored `last_activity_at` that is explicitly 2 hours in the past before the second scan.
- `test_config_bad_value_type_falls_back_to_default_and_logs` now covers bool — good — but also verify the log records WHY (a line containing `stale_threshold_seconds` and either `type` or `bool` so the user can diagnose from the log alone).
- `cst resume` does not validate that the `session_id` resolved to the record matches the one it interpolates into `claude --resume` — this is a paranoia check; the prefix resolver already guarantees it. No amendment; just worth a one-line assert in resume.py.

Generator may now begin coding Sprint 2.

RE_REVIEW_READY: sprint_contract.md
