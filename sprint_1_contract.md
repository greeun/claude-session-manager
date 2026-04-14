# Sprint 1 Contract — Claude Session Manager

## 1. Sprint 1 goal

Deliver an end-to-end "tracer bullet" of the core registry loop: a `cst` CLI that can scan `~/.claude/projects/`, create per-session JSON records, list and mutate them, with working `session-start` and `activity` hook entry points and an idempotent installer that wires everything into `~/.claude/settings.json`.

## 2. Features in this sprint

The slice below targets specific Definition of Done bullets from `spec.md` §8.

- **Registry storage (per-session JSON files under `~/.claude/claude-tasks/`)**
  - Atomic per-record writes (temp file + `os.replace`).
  - Per-record corrupt-file isolation: a malformed JSON file is renamed to `<id>.json.corrupt-<timestamp>` and reads of siblings continue.
  - Satisfies DoD bullets: *"Corrupting a single registry record on disk does not prevent `cst list` ... from functioning ..."* and *"All `cst` subcommands ... no subcommand leaves the registry partially written."*

- **Scanner (minimal)**
  - Walks `~/.claude/projects/*/*.jsonl`. The filename stem must match the canonical UUID regex `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`; files whose stem does not match are silently skipped (not treated as sessions, no record created, no crash).
  - For each valid UUID file, upserts records with `auto_detected=true`, `cwd` (from the first JSONL line's `cwd` field if present, else `null`), `project_name` (see decoding rule below), `last_activity_at` from file mtime, and a seeded `title`.
  - **Title seeding rule:** scan JSONL lines in order; pick the FIRST line whose `type == "user"` and whose `message.content` (or `content`) contains extractable plain text; trim to 60 chars. If no such line exists (empty file, assistant-only transcript, malformed), fall back to `project_name`. A first-line-is-assistant transcript must still yield the later user message's text.
  - **Project-slug → `project_name` decoding rule (binding):** Claude Code encodes the project directory path with forward slashes replaced by `-` and a leading `-` for the root slash, e.g. `/tmp/fake-proj` → `-tmp-fake-proj`. The scanner decodes this by stripping exactly one leading `-` and replacing the remaining `-` characters with `/`, then taking the basename. So `-tmp-fake-proj` → `/tmp/fake-proj` → `fake-proj`. If decoding yields an empty basename, `project_name` defaults to the raw slug. This contract is fixed for Sprint 1 so Sprint 2's focus/resume can rely on it.
  - Never overwrites user-owned fields (`title`, `priority`, `status`, `note`, `tags`) once `auto_detected=false`.
  - Never transitions records to `archived` or deletes anything.
  - Satisfies DoD bullets: *"Opening a brand-new terminal window and running `claude` causes a new session record to appear ..."*, *"A newly detected session with no user title has a non-empty title ..."*, and *"Running `/task-register` ... flipped auto_detected to false; subsequent scans no longer overwrite ..."* (the registry + scanner side; slash command itself deferred — user can simulate via `cst set` which is included).

- **CLI subcommands delivered in Sprint 1**
  - `cst list` — prints one row per non-archived session, sorted by priority (high→medium→low) then `last_activity_at` desc. Output format (binding): tab-separated columns in this exact order — `short_id` (first 8 hex chars of `session_id`), `priority` (one of `high`/`medium`/`low`), `status`, `title`, `project_name`, `relative_time`. Exactly one row per session; no header row; `(no sessions)` printed when the registry is empty. Exit 0 on empty registry. Satisfies DoD: *"After install, `cst list` exits 0 even when the registry is empty."* and *"`cst list` rows are ordered by priority (high→low) then by most-recent activity."*
  - `cst list --all` — includes archived.
  - `cst list --json` — emits a JSON array of records in the same sort order. Each element contains at minimum `session_id`, `short_id`, `priority`, `status`, `title`, `project_name`, `last_activity_at`, `archived`. This is the machine-checkable representation tests and checks rely on for ordering assertions.
  - `cst --version` — prints the skill version string (Sprint 1 = `cst 0.1.0`) to stdout and exits 0.
  - `cst set <id> [--title ...] [--priority ...] [--status ...] [--note ...] [--tags a,b]` — mutates a record and flips `auto_detected=false`. Satisfies cross-surface parity DoD bullet (write half).
  - `cst done <id>` — sets `status=done`, keeps record visible.
  - `cst archive <id>` — sets `archived=true`, `archived_at=now`.
  - `cst scan` — forces a scan pass and prints a one-line summary (`scanned N, created M, updated K`).

- **Hook entry points**

  **Hook stdin payload contract (binding).** Per Claude Code's hooks documentation, both `SessionStart` and `UserPromptSubmit` hooks deliver a JSON payload on stdin containing at minimum the fields `session_id`, `cwd`, `hook_event_name`, and `transcript_path`. Our hook commands MUST parse stdin as JSON FIRST, and use environment variables (`CLAUDE_SESSION_ID`, `CLAUDE_PROJECT_DIR`, `PWD`) only as a fallback when stdin is empty, not a TTY, or not valid JSON. This is load-bearing: env-var-only parsing would silently no-op in real Claude Code use.

  - `cst hook session-start` — reads stdin as JSON (`session_id`, `cwd`, `transcript_path`) with env vars (`CLAUDE_SESSION_ID`, `CLAUDE_PROJECT_DIR` / `PWD`) as fallback. Upserts a record; best-effort terminal capture (`TERM_PROGRAM` → `terminal.app`, `tty` via `os.ttyname(0)` when available). Always exits 0 even on exception. On any failure path (missing id, write error, malformed stdin), appends a timestamped line naming the exception class / missing field to `~/.claude/claude-tasks/.hook-errors.log` before exiting 0. Satisfies DoD: *"Failing hooks do not block the user's Claude Code session."*
  - `cst hook activity` — reads stdin as JSON (`session_id`) with `CLAUDE_SESSION_ID` env var as fallback. Touches `last_activity_at`. Exits 0 unconditionally. Same error-logging contract.

- **Installer (`install.sh`)**
  - Creates symlink: `ln -sfn "$(pwd)" ~/.claude/skills/claude-session-manager`.
  - Creates `~/.local/bin/cst` symlink to `scripts/cst.py` (and `chmod +x`). Warns if `~/.local/bin` not in PATH.
  - Creates `~/.claude/` and `~/.claude/claude-tasks/` if missing. If `~/.claude/settings.json` does not exist, creates it with `{}` before merging.
  - Idempotently merges into `~/.claude/settings.json`:
    - Hook-merge idempotency rule (binding): a hook entry is considered "already present" iff there exists a `hooks[<Event>][].hooks[].command` value whose full string is EXACTLY equal to the literal the installer would insert (`cst hook session-start` or `cst hook activity`). Substring matching is forbidden; unrelated entries like `cst hook session-start --debug` are NOT treated as duplicates and must not prevent insertion.
    - Appends `SessionStart` and `UserPromptSubmit` hook entries calling `cst hook session-start` / `cst hook activity` iff not already present per the rule above.
    - Does NOT touch existing `statusLine` key. (Statusline wiring deferred to Sprint 2/3.)
  - **Malformed settings.json policy (choice (a), binding):** if `~/.claude/settings.json` exists but is not parseable as JSON, the installer prints a clear error message naming the file and the parse error, exits with code 2, and does NOT modify the file or write a backup. This is intentionally conservative: a user with a hand-edited broken file gets a chance to repair it before automation touches it. (Rationale: silent rewrite is unsafe; automatic backup adds complexity without eliminating the need for the user to fix their file.)
  - Smoke test at end: runs `cst list` via its absolute path and reports exit code; installer exits non-zero if smoke test fails.
  - Satisfies DoD: *"Running the installer exactly once exposes a working `cst` command ..."*, *"Running the installer a second time produces no duplicate hook entries ..."*, *"After install, `cst list` exits 0 ..."*

## 3. Features explicitly deferred

| Feature | Target sprint |
|---|---|
| `cst focus` / AppleScript per-terminal support | Sprint 2 |
| `cst resume` (new terminal + `claude --resume`) | Sprint 2 |
| `cst statusline` + statusline installer wiring | Sprint 2 |
| Live-vs-idle dot (ps/tty matching) in `cst list` | Sprint 2 |
| Stale detection banner in list + `cst review-stale` | Sprint 2 |
| Slash commands (`/tasks`, `/task-register`, `/task-note`, `/task-priority`, `/task-status`, `/task-done`, `/task-focus`) | Sprint 3 |
| `cst watch` TUI (rich-based) | Sprint 3 |
| `cst watch --pin` dedicated window | Sprint 4 |
| `cst gc` (7-day archived deletion) | Sprint 2 |
| Short-id prefix matching ≥6 chars with ambiguity handling | Sprint 2 |
| Config file for stale threshold (`~/.claude/claude-tasks.config.json`) | Sprint 2 |
| macOS-only platform guard | Sprint 2 |

Sprint 1 accepts full-length `session_id` only; any other id format returns a "not found" error.

## 4. How to run

All commands assume the Evaluator `cd`s into this skill directory first.

### 4.1 Install

```bash
bash install.sh
```

Expected: exit 0; prints a "smoke test PASSED" line; creates `~/.local/bin/cst`, `~/.claude/skills/claude-session-manager` symlink, `~/.claude/claude-tasks/` dir, and inserts hook entries into `~/.claude/settings.json`.

Rerunning the same command must also exit 0 and must not duplicate hook entries.

### 4.2 Exercise the CLI directly

```bash
# With a freshly installed, empty registry:
cst list                               # exits 0, prints "(no sessions)"
cst scan                               # scans ~/.claude/projects/, prints summary

# Manually inject a fake session record by invoking the hook the way Claude Code will:
CLAUDE_SESSION_ID=11111111-1111-1111-1111-111111111111 \
  CLAUDE_PROJECT_DIR=/tmp/fake-proj \
  cst hook session-start

cst list                               # shows one row for that session
cst set 11111111-1111-1111-1111-111111111111 --title "Demo" --priority high
cst list                               # row now shows title "Demo" and priority high

# Touch activity:
CLAUDE_SESSION_ID=11111111-1111-1111-1111-111111111111 \
  cst hook activity
cst list                               # last_activity relative-time is ~0s

cst done    11111111-1111-1111-1111-111111111111   # status→done, still visible
cst archive 11111111-1111-1111-1111-111111111111   # hidden from default list
cst list                               # no rows
cst list --all                         # archived row reappears
```

### 4.3 Simulate a Claude Code session WITHOUT running `claude`

The Evaluator creates a fake project transcript:

```bash
mkdir -p ~/.claude/projects/-tmp-fake-proj
SID="22222222-2222-2222-2222-222222222222"
cat > ~/.claude/projects/-tmp-fake-proj/${SID}.jsonl <<'EOF'
{"type":"user","message":{"role":"user","content":"Refactor the login endpoint"},"cwd":"/tmp/fake-proj","sessionId":"22222222-2222-2222-2222-222222222222"}
EOF
cst scan
cst list                               # shows a row with title starting "Refactor the login endpoint"
```

### 4.4 Trigger hooks the way Claude Code would

The installer wires `~/.claude/settings.json` such that `SessionStart` and `UserPromptSubmit` run `cst hook ...`. The Evaluator can verify this by inspecting settings.json (see check 4 below) or by invoking the commands manually with the same env vars Claude Code sets.

## 5. Observable verification checks

Each check is stricter than the relevant DoD bullet.

### Check 1 — Installer is idempotent and wires hooks exactly once

```bash
bash install.sh
bash install.sh
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path.home().joinpath('.claude/settings.json').read_text())
hooks = s.get('hooks', {})
ss_cmds = [h['command'] for m in hooks.get('SessionStart', []) for h in m.get('hooks', [])]
up_cmds = [h['command'] for m in hooks.get('UserPromptSubmit', []) for h in m.get('hooks', [])]
# Exact full-string equality — substring matching is not sufficient.
assert ss_cmds.count('cst hook session-start') == 1, ss_cmds
assert up_cmds.count('cst hook activity') == 1, up_cmds
print('HOOKS_OK')
"
```

**Expected**: prints `HOOKS_OK` and exits 0.

### Check 2 — `cst list` on empty registry

```bash
rm -rf ~/.claude/claude-tasks && mkdir -p ~/.claude/claude-tasks
cst list; echo "exit=$?"
```

**Expected**: exit 0, output does not crash or raise, clearly indicates zero rows (e.g. `(no sessions)` or empty table).

### Check 3 — Scanner creates records from a fixture JSONL

```bash
# (setup from 4.3 above)
cst scan
ls ~/.claude/claude-tasks/22222222-2222-2222-2222-222222222222.json
python3 -c "
import json, pathlib
r = json.loads(pathlib.Path.home().joinpath('.claude/claude-tasks/22222222-2222-2222-2222-222222222222.json').read_text())
assert r['session_id'] == '22222222-2222-2222-2222-222222222222'
assert r['auto_detected'] is True
assert r['title'] and len(r['title']) <= 60
assert r['archived'] is False
print('SCAN_OK', r['title'])
"
```

**Expected**: prints `SCAN_OK <title>`, with a non-empty title derived from the first user message.

### Check 4 — User edit is sticky across subsequent scans (title, priority, status, note, tags)

```bash
cst set 22222222-2222-2222-2222-222222222222 \
    --title "User Title" --priority high --status blocked \
    --note "keep me" --tags a,b
cst scan
python3 -c "
import json, pathlib
r = json.loads(pathlib.Path.home().joinpath('.claude/claude-tasks/22222222-2222-2222-2222-222222222222.json').read_text())
assert r['title'] == 'User Title', r
assert r['priority'] == 'high', r
assert r['status'] == 'blocked', r
assert r['note'] == 'keep me', r
assert r['tags'] == ['a','b'], r
assert r['auto_detected'] is False, r
print('STICKY_OK')
"
```

**Expected**: prints `STICKY_OK`. The scanner must not overwrite any of `title`, `priority`, `status`, `note`, `tags` once `auto_detected=false`.

### Check 5 — Corrupt file isolation (and original bytes preserved)

```bash
orig_bytes='{this is not valid json'
printf '%s' "$orig_bytes" > ~/.claude/claude-tasks/badfile.json
cst list > /tmp/cst_list_out.txt; echo "exit=$?"
# Good siblings must still render:
grep -q 22222222 /tmp/cst_list_out.txt || { echo FAIL_GOOD_SIBLING_MISSING; exit 1; }
renamed=$(ls ~/.claude/claude-tasks/ | grep -E '^badfile\.json\.corrupt-[0-9]+$' | head -n1)
test -n "$renamed" || { echo FAIL_NO_RENAME; exit 1; }
# Bytes must be byte-identical to what we wrote:
got=$(cat "$HOME/.claude/claude-tasks/$renamed")
[ "$got" = "$orig_bytes" ] || { echo FAIL_BYTES_CHANGED; exit 1; }
echo CORRUPT_BYTES_PRESERVED
```

**Expected**: `cst list` exits 0, good sibling rows still render, the bad file is renamed to `badfile.json.corrupt-<timestamp>`, and the renamed file's bytes are byte-identical to the original malformed content.

### Check 6 — Hook exit code is 0 even on failure, and the error is logged

```bash
: > ~/.claude/claude-tasks/.hook-errors.log 2>/dev/null || true
rm -f ~/.claude/claude-tasks/.hook-errors.log
# Invoke with no env vars AND no stdin payload; the hook must still exit 0
env -i PATH="$PATH" cst hook session-start < /dev/null; rc1=$?
env -i PATH="$PATH" cst hook activity       < /dev/null; rc2=$?
[ "$rc1" = "0" ] && [ "$rc2" = "0" ] || { echo FAIL_EXIT_NONZERO; exit 1; }
# Log file must exist and contain at least one recent entry naming the missing field / exception.
test -s ~/.claude/claude-tasks/.hook-errors.log || { echo FAIL_LOG_MISSING; exit 1; }
python3 -c "
import pathlib, re, time, datetime
txt = pathlib.Path.home().joinpath('.claude/claude-tasks/.hook-errors.log').read_text()
assert txt.strip(), 'log empty'
# Expect an ISO-8601 timestamp within last 60 seconds on at least one line.
now = datetime.datetime.utcnow()
fresh = False
for line in txt.splitlines():
    m = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
    if not m: continue
    t = datetime.datetime.strptime(m.group(1), '%Y-%m-%dT%H:%M:%S')
    if (now - t).total_seconds() < 60:
        fresh = True; break
assert fresh, 'no fresh timestamped line'
assert 'session_id' in txt or 'Exception' in txt or 'KeyError' in txt or 'missing' in txt.lower()
print('HOOK_LOG_OK')
"
```

**Expected**: both hook invocations exit 0, `.hook-errors.log` exists, and contains at least one line timestamped within the last 60 seconds naming either the missing field or the exception class. Prints `HOOK_LOG_OK`.

### Check 7 — Sort order is machine-checkable via `cst list --json`

Seed three records with distinct priorities and activity times, then assert on parsed JSON:

```bash
python3 -c "
import json, pathlib, datetime, os
d = pathlib.Path(os.path.expanduser('~/.claude/claude-tasks'))
d.mkdir(parents=True, exist_ok=True)
def rec(sid, pri, ago_s, title):
    ts = (datetime.datetime.utcnow() - datetime.timedelta(seconds=ago_s)).strftime('%Y-%m-%dT%H:%M:%SZ')
    (d / f'{sid}.json').write_text(json.dumps({
        'session_id': sid, 'title': title, 'priority': pri, 'status': 'in_progress',
        'cwd': '/tmp/x', 'project_name': 'x', 'tags': [], 'note': '',
        'created_at': ts, 'last_activity_at': ts,
        'terminal': {'app': None, 'window_id': None, 'tab_id': None, 'tty': None},
        'auto_detected': True, 'archived': False, 'archived_at': None,
    }))
rec('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'medium', 10,  'M-recent')
rec('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'high',   300, 'H-old')
rec('cccccccc-cccc-cccc-cccc-cccccccccccc', 'high',   5,   'H-recent')
rec('dddddddd-dddd-dddd-dddd-dddddddddddd', 'low',    1,   'L-newest')
"
cst list --json > /tmp/cst_list.json
python3 -c "
import json
rows = json.load(open('/tmp/cst_list.json'))
titles = [r['title'] for r in rows]
assert titles == ['H-recent', 'H-old', 'M-recent', 'L-newest'], titles
print('SORT_OK')
"
```

**Expected**: prints `SORT_OK`. Ordering is `high → medium → low`; within a priority, more recent `last_activity_at` comes first.

### Check 8 — Atomic writes / no partial files

```bash
# Simulated by forcing a write and examining directory contents. Registry writes use
# tempfile + os.replace; at no point should a zero-byte <id>.json exist.
# The pytest suite (see §6) covers this by monkeypatching os.replace to raise and
# asserting the destination file is either absent or unchanged.
pytest -q tests/test_registry.py::test_atomic_write_no_partial
```

**Expected**: test passes.

### Check 9 — `done` keeps visible, `archive` hides (hard assertions)

```bash
cst done 22222222-2222-2222-2222-222222222222
[ "$(cst list | grep -c 22222222)" = "1" ] || { echo FAIL_DONE_HIDDEN; exit 1; }
cst archive 22222222-2222-2222-2222-222222222222
[ "$(cst list | grep -c 22222222)" = "0" ] || { echo FAIL_ARCHIVE_VISIBLE; exit 1; }
[ "$(cst list --all | grep -c 22222222)" = "1" ] || { echo FAIL_ALL_MISSING; exit 1; }
echo LIFECYCLE_OK
```

**Expected**: prints `LIFECYCLE_OK`. Any failed assertion aborts with a `FAIL_*` message and non-zero exit.

### Check 11 — Two distinct session ids produce two distinct records

```bash
rm -f ~/.claude/claude-tasks/33333333-*.json ~/.claude/claude-tasks/44444444-*.json
CLAUDE_SESSION_ID=33333333-3333-3333-3333-333333333333 CLAUDE_PROJECT_DIR=/tmp/a \
    cst hook session-start < /dev/null
CLAUDE_SESSION_ID=44444444-4444-4444-4444-444444444444 CLAUDE_PROJECT_DIR=/tmp/b \
    cst hook session-start < /dev/null
n=$(ls ~/.claude/claude-tasks/ | grep -cE '^(33333333|44444444)-[0-9a-f-]+\.json$')
[ "$n" = "2" ] || { echo FAIL_MERGE; exit 1; }
m=$(cst list --json | python3 -c "import json,sys; rows=json.load(sys.stdin); print(sum(1 for r in rows if r['session_id'].startswith(('33333333','44444444'))))")
[ "$m" = "2" ] || { echo FAIL_LIST_MERGE; exit 1; }
echo DISTINCT_IDS_OK
```

**Expected**: prints `DISTINCT_IDS_OK`. Guards against a bug where both hook invocations collapse into a single record.

### Check 12 — Installer works from a truly fresh `$HOME`

```bash
TMPH=$(mktemp -d)
HOME="$TMPH" bash install.sh
test -f "$TMPH/.claude/settings.json" || { echo FAIL_NO_SETTINGS; exit 1; }
test -d "$TMPH/.claude/claude-tasks"  || { echo FAIL_NO_TASKDIR;  exit 1; }
HOME="$TMPH" python3 -c "
import json, os, pathlib
s = json.loads(pathlib.Path(os.environ['HOME'], '.claude/settings.json').read_text())
assert 'SessionStart'      in s.get('hooks', {}), s
assert 'UserPromptSubmit'  in s.get('hooks', {}), s
print('FRESH_OK')
"
```

**Expected**: prints `FRESH_OK`. Covers the clean-machine DoD bullet — no pre-existing `~/.claude` or `settings.json`.

### Check 13 — Installer refuses to touch a malformed `settings.json`

Per §2, policy (a): the installer exits non-zero and does NOT modify the file.

```bash
TMPH=$(mktemp -d); mkdir -p "$TMPH/.claude"
echo '{not: valid' > "$TMPH/.claude/settings.json"
orig=$(cat "$TMPH/.claude/settings.json")
HOME="$TMPH" bash install.sh; rc=$?
now=$(cat "$TMPH/.claude/settings.json")
[ "$rc" != "0" ]    || { echo FAIL_INSTALLER_SILENT; exit 1; }
[ "$orig" = "$now" ] || { echo FAIL_INSTALLER_OVERWROTE; exit 1; }
# And no settings.json.bak-* was written (policy (a), not (b)):
! ls "$TMPH/.claude/" | grep -qE '^settings\.json\.bak-' || { echo FAIL_UNEXPECTED_BACKUP; exit 1; }
echo MALFORMED_SETTINGS_RESPECTED
```

**Expected**: prints `MALFORMED_SETTINGS_RESPECTED`. The installer must exit non-zero AND leave the malformed file byte-identical AND not create any backup file.

### Check 14 — Hook reads stdin JSON payload (env-less)

```bash
rm -f ~/.claude/claude-tasks/55555555-*.json
echo '{"session_id":"55555555-5555-5555-5555-555555555555","cwd":"/tmp/x","hook_event_name":"SessionStart","transcript_path":"/tmp/x.jsonl"}' \
  | env -i PATH="$PATH" cst hook session-start
test -f ~/.claude/claude-tasks/55555555-5555-5555-5555-555555555555.json || { echo FAIL_STDIN_NOOP; exit 1; }
python3 -c "
import json, pathlib
r = json.loads(pathlib.Path.home().joinpath('.claude/claude-tasks/55555555-5555-5555-5555-555555555555.json').read_text())
assert r['session_id'] == '55555555-5555-5555-5555-555555555555'
assert r['cwd'] == '/tmp/x'
print('STDIN_OK')
"
# Activity hook on stdin too:
echo '{"session_id":"55555555-5555-5555-5555-555555555555","hook_event_name":"UserPromptSubmit"}' \
  | env -i PATH="$PATH" cst hook activity
echo STDIN_CHECKS_OK
```

**Expected**: prints `STDIN_OK` then `STDIN_CHECKS_OK`. Confirms that the hook parses stdin JSON without any env vars set.

### Check 10 — Installer did not overwrite an existing statusLine

```bash
# Pre-seed ~/.claude/settings.json with a dummy statusLine, then rerun installer,
# then assert the dummy is still present.
python3 -c "
import json, pathlib
p = pathlib.Path.home()/'.claude/settings.json'
s = json.loads(p.read_text()) if p.exists() else {}
s['statusLine'] = {'type':'command','command':'echo MY_EXISTING_STATUSLINE'}
p.write_text(json.dumps(s))
"
bash install.sh
python3 -c "
import json, pathlib
s = json.loads((pathlib.Path.home()/'.claude/settings.json').read_text())
assert s['statusLine']['command'] == 'echo MY_EXISTING_STATUSLINE', s
print('STATUSLINE_UNTOUCHED')
"
```

**Expected**: prints `STATUSLINE_UNTOUCHED`.

## 6. Test harness

Pytest suite under `tests/`:

- `tests/test_registry.py`
  - `test_create_read_roundtrip`
  - `test_update_flips_auto_detected`
  - `test_atomic_write_no_partial` — patches `os.replace` to raise on first call, asserts no partial file left behind.
  - `test_corrupt_file_isolated` — writes bad JSON alongside good, asserts `list_all()` returns only the good one and the bad one is renamed.
  - `test_concurrent_writes` — two threads writing distinct records must both succeed.

- `tests/test_scanner.py`
  - `test_creates_draft_from_jsonl` — fixture JSONL with one user message yields a record with the expected title.
  - `test_title_falls_back_to_project_name` — JSONL with no user message yields title == project_name.
  - `test_title_from_later_user_message_when_first_is_assistant` — first line is assistant, second is user; title comes from the user line.
  - `test_empty_jsonl_falls_back_to_project_name` — zero-byte file: title == project_name, no crash.
  - `test_non_uuid_filename_is_ignored` — a `notauuid.jsonl` file creates no record and does not crash.
  - `test_updates_last_activity_from_mtime`
  - `test_never_overwrites_user_fields` — pre-seed a record with `auto_detected=False` and non-default `title`, `priority`, `status`, `note`, `tags`; rescan; assert ALL FIVE fields unchanged.
  - `test_never_touches_archived` — archived record's `archived` flag survives rescan.
  - `test_project_name_derivation` — slug `-tmp-fake-proj` → `project_name == 'fake-proj'`; slug `-Users-alice-proj-foo` → `'foo'`; empty / odd slug falls back to the raw slug.

- `tests/test_cli.py`
  - `test_list_empty_exits_zero`
  - `test_set_then_list` — invokes CLI via `subprocess.run([sys.executable, 'scripts/cst.py', ...])`.
  - `test_done_visible_archive_hidden`
  - `test_list_sort_order_high_medium_low_then_recency` — seeds four records across priorities + mtimes, asserts parsed `cst list --json` order matches `[high-recent, high-old, medium-*, low-*]`.
  - `test_list_json_schema` — each emitted JSON element contains the binding keys (`session_id`, `short_id`, `priority`, `status`, `title`, `project_name`, `last_activity_at`, `archived`).
  - `test_version_flag` — `cst --version` prints `cst 0.1.0` and exits 0.

- `tests/test_hooks.py`
  - `test_session_start_hook_creates_record`
  - `test_session_start_hook_exits_zero_on_missing_env` — no env, no stdin: exit 0 AND log file contains a fresh timestamped line.
  - `test_activity_hook_touches_last_activity_at`
  - `test_session_start_reads_stdin_payload` — JSON on stdin with `session_id`, `cwd`, `transcript_path`, no env vars; record is created with those values.
  - `test_activity_reads_stdin_payload` — JSON on stdin with `session_id` only, no env; `last_activity_at` is touched.
  - `test_stdin_takes_priority_over_env` — when both stdin JSON and env vars specify different `session_id`s, the stdin value wins.
  - `test_distinct_session_ids_create_distinct_records` — two invocations with different `session_id`s produce two files, no merging.

- `tests/test_installer.py`
  - `test_install_idempotent` — runs `install.sh` twice against a temp `HOME`, asserts each of `cst hook session-start` and `cst hook activity` appears exactly once with full-string equality.
  - `test_install_preserves_existing_statusline`
  - `test_install_from_missing_settings_json` — fresh `$HOME` with no `.claude/` at all: installer creates both `.claude/` and `settings.json` and inserts both hook events.
  - `test_install_from_missing_claude_dir` — `$HOME` exists but `.claude/` does not: installer creates it.
  - `test_install_refuses_malformed_settings_json` — pre-seed `settings.json` with invalid JSON; installer exits non-zero, file bytes unchanged, no `settings.json.bak-*` created.
  - `test_install_does_not_treat_substring_match_as_duplicate` — pre-seed `settings.json` with a hook command `cst hook session-start --debug`; installer must still insert the exact `cst hook session-start` entry alongside it.

Run:

```bash
pytest -q
```

All tests use `tmp_path` and a monkeypatched `HOME` / `CST_REGISTRY_DIR` so they never touch the real `~/.claude`. `tests/conftest.py` additionally asserts at collection time that the test process's `HOME` has been redirected to a `tmp_path` under pytest's basetemp; if a test ever reaches the registry code with the real user `HOME`, the fixture raises `RuntimeError` before any I/O runs.

## 7. Stack / tooling decisions

- **Python 3.11+** (ships with macOS 14+ or via Homebrew; matches repo's `python3`).
- **Stdlib-only for Sprint 1**: `argparse`, `json`, `pathlib`, `os`, `tempfile`, `subprocess`, `datetime`, `uuid`, `re`. `fcntl` is explicitly NOT used in Sprint 1: since each session record lives in its own file and `os.replace` is atomic on POSIX, no advisory locks are needed for the tests and checks we ship. (If a future sprint requires same-record concurrent safety, `fcntl` can be reintroduced then.)
- **No rich / blessed / click** in Sprint 1 — plain text table via string formatting and `json.dumps` for `--json`. `rich` is introduced in Sprint 3 with the `watch` TUI.
- **Shell boundary**: `install.sh` is bash; it only does symlinks, directory creation, and delegates JSON merging to a `python3 -m` call into an installer module that uses stdlib `json` (atomic write via tempfile + rename). Malformed-JSON refusal is implemented in Python and the shell wrapper propagates its exit code.
- **Entry point**: `scripts/cst.py` with a `main()` that dispatches on `argv[1]` (`list`, `set`, `done`, `archive`, `scan`, `hook`) plus `--version`. `~/.local/bin/cst` is a symlink to this file; file has a `#!/usr/bin/env python3` shebang.
- **Registry path**: `~/.claude/claude-tasks/` by default, overridable via `CST_REGISTRY_DIR` env var (used exclusively by tests).
- **Version string**: `cst 0.1.0` — exposed via `cst --version` and by a `__version__` constant in `scripts/cst.py`.
- **No third-party deps declared**; `pyyaml` is NOT needed this sprint.

## 8. File layout after Sprint 1

```
claude-session-manager/
├── spec.md                      # (existing, untouched)
├── _brainstorm-design.md        # (existing, untouched)
├── sprint_contract.md           # this file
├── SKILL.md                     # minimal frontmatter + pointer to cst; not expanded this sprint
├── install.sh
├── scripts/
│   ├── cst.py                   # CLI entry point; dispatches subcommands
│   ├── registry.py              # per-file JSON CRUD, atomic write, corruption isolation
│   ├── scanner.py               # ~/.claude/projects/*.jsonl → registry upsert
│   └── hooks.py                 # session-start and activity handlers (imported by cst.py)
└── tests/
    ├── conftest.py              # tmp HOME / CST_REGISTRY_DIR fixtures
    ├── test_registry.py
    ├── test_scanner.py
    ├── test_cli.py
    ├── test_hooks.py
    └── test_installer.py
```

No `references/`, `assets/`, or `commands/` directories in Sprint 1 — those arrive in Sprints 2 and 3.

## 9. Non-goals for this sprint

Focus/AppleScript, `cst resume`, `cst watch` TUI, statusline command and wiring, slash commands, live-vs-idle dot, stale detection + banner + `review-stale`, `cst gc`, `--pin` window, short-id prefix matching, stale-threshold config file, macOS platform guard — all deferred per §3.

## 10. Risks and assumptions

1. **Hook payload source of truth**: the binding contract (see §2 "Hook stdin payload contract") is that Claude Code delivers a JSON payload on stdin with fields `session_id`, `cwd`, `hook_event_name`, `transcript_path`. Our implementation parses stdin FIRST and treats env vars (`CLAUDE_SESSION_ID`, `CLAUDE_PROJECT_DIR`, `PWD`) as fallback only. If both are absent, the hook logs to `~/.claude/claude-tasks/.hook-errors.log` and exits 0. If both are present but disagree, stdin wins (verified by `test_stdin_takes_priority_over_env`).
2. **`~/.claude/projects/` directory layout**: the scanner assumes `<project-slug>/<session-uuid>.jsonl`. If the layout differs on the Evaluator's machine, scanner tests still pass (they use fixtures), but `cst scan` against a real `~/.claude` may produce zero records. This is acceptable for Sprint 1 since the hook-based path also populates the registry.
3. **JSONL `cwd` field**: not always present. When absent, `cwd` is stored as `null`; downstream sprints (focus/resume) will need to handle this.
4. **Symlink to `~/.local/bin/cst`**: if the directory does not exist the installer creates it. If `~/.local/bin` is not in `$PATH` the installer prints an actionable warning but exits 0 — the smoke test `cst list` is run via an absolute path so the install still passes.
5. **Concurrency**: per-file atomic writes via `os.replace` are sufficient because each record lives in its own file. We do not serialize writes across different records; the test suite verifies this is safe.
6. **`settings.json` may not exist**: the installer creates it with `{}` before merging. If it exists but is unparseable, the installer picks policy (a) per §2: exit non-zero, no overwrite, no backup.
7. **The installer must not require `sudo`**: everything lives under `$HOME`.
8. **Project-slug decoding is a best-effort heuristic**: the rule defined in §2 handles the common `/abs/path` case; slugs from unusual paths (containing literal `-` in directory names) may decode to a wrong path. For Sprint 1 this only affects the cosmetic `project_name`; Sprint 2 (focus/resume) will need to rely on `cwd` captured directly from hooks/JSONL meta, not on the decoded slug.
9. **Hook-merge matcher strictness**: the installer uses full-string equality on the command literal, so users who manually wrap our hook (e.g. `sh -c 'cst hook activity && mything'`) will get a duplicate-looking entry on re-install. This is acceptable for Sprint 1; a smarter matcher is out of scope.

SPRINT_CONTRACT_READY: sprint_contract.md

## 11. Evaluator amendments (binding)

**Verdict: AMEND**

The contract covers the major DoD bullets it claims to cover, but several checks are weaker than the corresponding DoD bullet or than what a skeptical user would demand, and three gaps are not addressed at all. The following amendments are required before coding begins. Each amendment cites either a DoD bullet from `spec.md` §8 or a concrete adversarial probe.

### Required amendments

1. **Extend Check 4 to cover `note`, `tags`, and `status`** (DoD: *"subsequent scans no longer overwrite the title, priority, status, note, or tags"*). Current Check 4 only probes `title` and `priority`. After `cst set ... --note "keep me" --tags a,b --status blocked`, the test must re-scan and assert all of `title`, `priority`, `status`, `note`, and `tags` survive unchanged. Mirror this in `tests/test_scanner.py::test_never_overwrites_user_fields`.

2. **Check 5 must verify the ORIGINAL BYTES survive in the renamed corrupt file** (spec Feature table: *"preserved for inspection"*). Replace the current `grep` on filename with:
   ```bash
   orig_bytes='{this is not valid json'
   renamed=$(ls ~/.claude/claude-tasks/ | grep -E '^badfile\.json\.corrupt-[0-9]+$' | head -n1)
   test -n "$renamed" || { echo FAIL_NO_RENAME; exit 1; }
   diff <(printf '%s\n' "$orig_bytes") "$HOME/.claude/claude-tasks/$renamed" || { echo FAIL_BYTES_CHANGED; exit 1; }
   echo CORRUPT_BYTES_PRESERVED
   ```
   Additionally assert `cst list` stdout contains rows from good sibling files in the same run.

3. **Check 6 must verify the error was logged, not just swallowed** (DoD: *"Failing hooks do not block the user's Claude Code session"* — but silent swallowing makes debugging impossible, and the contract itself promises the log file). After the two `env -i` calls, assert `~/.claude/claude-tasks/.hook-errors.log` exists and contains a non-empty line timestamped within the last 60 seconds mentioning the missing env var or exception class. Mirror in `tests/test_hooks.py::test_session_start_hook_exits_zero_on_missing_env`.

4. **Add Check 11 — two distinct `CLAUDE_SESSION_ID`s produce two distinct records.** A scanner or hook that silently merges under one id would pass every current check. Probe:
   ```bash
   CLAUDE_SESSION_ID=33333333-3333-3333-3333-333333333333 CLAUDE_PROJECT_DIR=/tmp/a cst hook session-start
   CLAUDE_SESSION_ID=44444444-4444-4444-4444-444444444444 CLAUDE_PROJECT_DIR=/tmp/b cst hook session-start
   ls ~/.claude/claude-tasks/ | grep -E '^(3{8}-|4{8}-)' | wc -l    # must be 2
   cst list | grep -cE '^(33333333|44444444)'                        # must be 2
   ```

5. **Add Check 12 — installer from a truly fresh state** (DoD: *"on a clean macOS machine"* and explicit risk §10.6). Currently no check covers the case where `~/.claude/settings.json` does not exist at all. Probe against a `HOME=$tmp` sandbox:
   ```bash
   TMPH=$(mktemp -d); HOME=$TMPH bash install.sh
   test -f "$TMPH/.claude/settings.json"
   python3 -c "import json,os; s=json.load(open(os.environ['TMPH']+'/.claude/settings.json')); \
     assert 'SessionStart' in s['hooks'] and 'UserPromptSubmit' in s['hooks']; print('FRESH_OK')"
   ```
   Add matching `tests/test_installer.py::test_install_from_missing_settings_json` and `::test_install_from_missing_claude_dir`.

6. **Add Check 13 — installer is defensive against malformed `settings.json`.** A user with a hand-edited broken settings file must not have it overwritten and must get a clear error. Probe:
   ```bash
   echo '{not: valid' > ~/.claude/settings.json
   bash install.sh; rc=$?
   # Installer must either: (a) exit non-zero with a clear message AND leave the malformed file untouched,
   # or (b) back it up to settings.json.bak-<ts> before rewriting. Silent overwrite is FAIL.
   ```
   Document which of (a) or (b) is chosen in §2 and test it.

7. **Clarify and test the hook stdin payload contract** (risk §10.1 is load-bearing, not just a risk). Claude Code's `SessionStart` and `UserPromptSubmit` hooks deliver a JSON payload on stdin with at least `session_id`, `cwd`, `hook_event_name`, and `transcript_path` fields. Env-var-only parsing will silently no-op in real use and break DoD *"new session record to appear in the registry within seconds"*. Required:
   - §2 must state explicitly: "`cst hook session-start` reads stdin as JSON (`session_id`, `cwd`, `transcript_path`) with env vars as fallback; `cst hook activity` reads stdin JSON (`session_id`) with env var fallback."
   - Add a check that pipes a representative JSON payload on stdin with no env vars set and confirms the record is created:
     ```bash
     echo '{"session_id":"55555555-5555-5555-5555-555555555555","cwd":"/tmp/x","hook_event_name":"SessionStart","transcript_path":"/tmp/x.jsonl"}' \
       | env -i PATH="$PATH" cst hook session-start
     test -f ~/.claude/claude-tasks/55555555-5555-5555-5555-555555555555.json
     ```
   - Add `tests/test_hooks.py::test_session_start_reads_stdin_payload` and `::test_activity_reads_stdin_payload`.

8. **Scanner edge-case tests** (DoD: *"A newly detected session with no user title has a non-empty title"* — must hold for all plausible transcript shapes):
   - `test_title_from_later_user_message_when_first_is_assistant` — JSONL whose first line is `{"type":"assistant",...}` then a `user` line: title must come from the `user` line, not blank.
   - `test_empty_jsonl_falls_back_to_project_name` — zero-byte file: title == project_name, no crash.
   - `test_non_uuid_filename_is_ignored` — `~/.claude/projects/x/notauuid.jsonl`: scanner must skip, not crash, not create a record.

9. **Strengthen Check 7 (sort order) with a machine-checkable assertion.** The current phrasing ("Evaluator will parse the output") is not reproducible. Either:
   - Freeze the output format enough to parse (e.g. short-id in column 1, priority in column 2, tab-separated), and document this in §2; OR
   - Add `cst list --json` for Sprint 1 and assert ordering on parsed JSON in the test harness.
   Add `tests/test_cli.py::test_list_sort_order_high_medium_low_then_recency` using the chosen representation.

10. **Fix Check 9 to actually assert** (currently uses `|| true` which swallows failures; `grep -c` returns 0 when no match which is masked). Replace with explicit assertions:
    ```bash
    cst done 22222222-2222-2222-2222-222222222222
    [ "$(cst list | grep -c 22222222)" = "1" ] || { echo FAIL_DONE_HIDDEN; exit 1; }
    cst archive 22222222-2222-2222-2222-222222222222
    [ "$(cst list | grep -c 22222222)" = "0" ] || { echo FAIL_ARCHIVE_VISIBLE; exit 1; }
    [ "$(cst list --all | grep -c 22222222)" = "1" ] || { echo FAIL_ALL_MISSING; exit 1; }
    echo LIFECYCLE_OK
    ```

11. **Document the project-slug → project_name decoding rule.** §2 states `project_name` comes "from the project-slug directory name" but Claude Code encodes paths like `/tmp/fake-proj` as `-tmp-fake-proj`. Specify the exact rule (e.g. "strip leading `-`, replace `-` with `/`" or "use slug as-is") and add `tests/test_scanner.py::test_project_name_derivation` so Sprint 2 focus/resume sprints have a stable contract to build on.

12. **Settings.json hook-merge idempotency must match by full command string, not substring.** §2 says "matched by command string" — make this exact (full-string equality on `hooks[].hooks[].command`). A substring match like `'session-start' in c` (as used in Check 1) would break if a user has an unrelated `cst hook session-start --debug` entry; the installer's matcher must be stricter than the check's matcher. Document the exact rule in §2.

### Out-of-scope polish notes (non-binding)

- Consider adding `cst --version` for Sprint 1 to make install verification trivial.
- `fcntl` advisory locks listed in §7 are mentioned but `test_concurrent_writes` only tests distinct records (where no lock is needed). Either drop `fcntl` from the stack list for Sprint 1 or add a same-record concurrent test.
- The conftest should fail loudly if `HOME` isn't redirected, to prevent a rogue test from ever touching the real `~/.claude`.

CONTRACT_REVIEW_READY: sprint_contract.md

## 12. Evaluator re-review

**Verdict: APPROVE**

All 12 binding amendments from §11 are substantively addressed. Per-amendment status:

- #1 ✓ satisfied — Check 4 (§5) now asserts `title`, `priority`, `status`, `note`, `tags` all survive; `test_never_overwrites_user_fields` updated to check all five.
- #2 ✓ satisfied — Check 5 does a byte-for-byte comparison (`[ "$got" = "$orig_bytes" ]`) on the renamed file, and also asserts good sibling rows still render in the same `cst list` invocation.
- #3 ✓ satisfied — Check 6 reads `.hook-errors.log`, asserts non-empty, asserts a timestamp within the last 60s, and asserts a line mentions `session_id`/`Exception`/`KeyError`/`missing`; `test_session_start_hook_exits_zero_on_missing_env` mirrors it.
- #4 ✓ satisfied — Check 11 runs two distinct-UUID hook invocations and asserts exactly 2 files and 2 rows in `cst list --json`.
- #5 ✓ satisfied — Check 12 runs `HOME=$TMPH bash install.sh` against a fresh dir and asserts both `.claude/settings.json` is created and both hook event keys are populated; `test_install_from_missing_settings_json` and `test_install_from_missing_claude_dir` added.
- #6 ✓ satisfied — §2 documents policy (a); Check 13 asserts non-zero exit, byte-identical file, and absence of any `settings.json.bak-*`.
- #7 ✓ satisfied — §2 includes a binding "Hook stdin payload contract" paragraph; Check 14 pipes JSON with `env -i` (no env vars set at all) and asserts the record is created; `test_session_start_reads_stdin_payload`, `test_activity_reads_stdin_payload`, and `test_stdin_takes_priority_over_env` added.
- #8 ✓ satisfied — §2 scanner section spells out the title-seeding rule; all three edge-case tests (`test_title_from_later_user_message_when_first_is_assistant`, `test_empty_jsonl_falls_back_to_project_name`, `test_non_uuid_filename_is_ignored`) are in `tests/test_scanner.py`.
- #9 ✓ satisfied — `cst list --json` is a binding Sprint 1 deliverable; Check 7 asserts the exact title ordering on parsed JSON; `test_list_sort_order_high_medium_low_then_recency` added.
- #10 ✓ satisfied — Check 9 uses `[ "$(... | grep -c)" = "N" ] || { echo FAIL_*; exit 1; }` with no `|| true` swallowing.
- #11 ✓ satisfied — §2 defines the exact decoding rule (strip one leading `-`, replace remaining `-` with `/`, take basename); `test_project_name_derivation` added with three slug cases; risk §10.8 acknowledges the ambiguity with literal-`-` directories as a Sprint-1 cosmetic limitation.
- #12 ✓ satisfied — §2 installer section defines full-string-equality matching and forbids substring; Check 1 uses `.count('cst hook session-start') == 1`; `test_install_does_not_treat_substring_match_as_duplicate` added.

### Non-blocking nits for the Generator to keep in mind while coding

- Check 6 uses `datetime.datetime.utcnow()` but the hook's logging code isn't pinned to UTC in the contract. Recommend logging in UTC (with a trailing `Z`) to keep this check non-flaky; if the implementation logs local time the check's 60s window can still pass in most timezones but will be brittle around DST transitions.
- Check 5 compares a no-trailing-newline write (`printf '%s'`) against `$(cat)` (which strips trailing newlines) — this works today but is load-bearing on shell semantics; consider `cmp` instead of string equality in a follow-up.
- Risk §10.9: users who wrap the hook with `sh -c '... && ...'` will get duplicates on reinstall. Acceptable for Sprint 1 per the contract, but worth surfacing in install output ("found N existing hook entries; appended 2 new entries") so users notice.

Generator may now begin coding Sprint 1.

RE_REVIEW_READY: sprint_contract.md
