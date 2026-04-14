# Product Spec: Claude Session Manager

## 1. One-line pitch

A local macOS tool that lets a power user see every Claude Code session they currently have running across many terminal windows, triage them by priority and staleness, and jump to the exact window that owns any session — all reachable identically from an external CLI/TUI, from inside Claude Code via slash commands, and from the Claude Code statusline.

## 2. Target user & core job-to-be-done

**User.** A developer on macOS who routinely runs several concurrent Claude Code sessions, each in its own terminal window (iTerm2, Terminal.app, Ghostty, Alacritty, WezTerm, etc.), across multiple projects.

**Three concrete pains this product exists to remove.**
1. "Which window is which task?" — too many terminal windows, no easy way to map a task in their head to a specific window.
2. "What's most important right now?" — priorities across windows blur together; nothing shows a ranked view.
3. "I never clean up." — abandoned or forgotten sessions accumulate indefinitely; the user wants visibility into stale work without anything being deleted behind their back.

**Core job.** "Show me every Claude Code session I have, ranked by priority and freshness, let me jump to the owning window in one action, and surface the ones I've forgotten about — without ever destroying my data automatically."

## 3. Primary user flows

### Flow A — New session appears automatically
1. User opens a new terminal window and runs `claude` in some project directory.
2. Within seconds, that session appears in the registry with a tentative title (derived from the project name or a short summary of the first user message), priority `medium`, status `in_progress`, the terminal app / window / tab / tty captured, and `auto_detected = true`.
3. No user action is required for the session to be visible from all three surfaces (external CLI/TUI, slash commands, statusline).

### Flow B — User enriches a session
1. Inside the Claude Code session, the user runs a slash command to set a real title, a priority, optional tags, and a note (e.g. `/task-register "Login API refactor" high`).
2. The registry record flips `auto_detected` to `false` and the scanner will never again overwrite the user's title / priority / status / note / tags.
3. The same edits can be performed externally from the `cst` CLI or the `cst watch` TUI, and they converge on the same record.

### Flow C — Find the window for a task (the flagship flow)
1. User sees `📋 3 pending · 1 stale` in the Claude Code statusline of whichever session they are currently in.
2. User runs `/tasks` inside Claude Code and sees a numbered list of every non-archived session ranked by priority then recency, each row showing live-vs-idle indicator, title, project, and how long since last activity.
3. User runs `/task-focus 2` (or `cst focus <id>` externally). The terminal window that owns session #2 is brought to the front.
4. If the target window no longer exists or the terminal app is not scriptable, the tool does not error out — it tells the user exactly why focus failed and offers a one-command alternative: open a new terminal window and resume that session via `claude --resume`.

### Flow D — Stale triage
1. Any time the registry is listed, sessions whose last activity is older than the stale threshold (default 4 hours) and whose status is still active are surfaced as `stale`.
2. The list output shows a banner like `⚠ 3 stale sessions — run 'cst review-stale'`.
3. `cst review-stale` walks the user through each stale entry one at a time with four choices: keep as-is, mark done, archive, or skip. Nothing is deleted.

### Flow E — Completion and eventual cleanup
1. When the user finishes a task, they explicitly mark it done (slash command inside, or `cst done <id>` outside). Done sessions stay visible until archived.
2. Archiving hides a session from the default list but keeps the record. Archived records are only shown with `cst list --all`.
3. `cst gc` is the ONLY cleanup action that deletes data. It removes registry records whose `archived_at` is older than 7 days. It never touches records that are not archived. It never runs automatically.

### Flow F — Always-on TUI (optional)
1. On a busy day, user launches a persistent watch window that shows the live registry, auto-refreshing every few seconds.
2. The watch surface supports keyboard navigation to focus, resume, edit priority / status / note, mark done, or archive the highlighted row.
3. A `--pin` variant opens the watch in a dedicated small terminal window positioned out of the way (best-effort on terminal apps that support scripted window creation; otherwise degrades to a normal window).

### Flow G — "What was I doing?" at-a-glance progress
1. User glances at `cst list` (or `/tasks` inside Claude Code) and, without focusing any window, immediately sees for every session: what they last asked Claude, what Claude was in the middle of saying/doing, and — when applicable — a short hint like `Running: pytest -q tests/` or `Editing: scripts/cst.py` describing the last tool action.
2. This context appears as dim sub-rows underneath each session's headline row. No user input is required to populate it; it is refreshed automatically as the user works (via the user-prompt-submit signal) and as the scanner reprocesses transcripts. Whichever signal is fresher wins.
3. When the user wants the richer view, opening the watch TUI and highlighting a row shows the full last assistant summary in a detail panel.
4. When the user wants machine-parseable output (scripts, CI, piping), they pass `--compact` to collapse each session back to a single line.

### Flow H — Install and uninstall
1. User runs the skill's installer once. It wires up: (a) a `cst` command reachable from their shell, (b) Claude Code hooks that record session start and user prompt activity, (c) the statusline command, (d) the slash commands. It creates the registry data directory if missing.
2. The installer is idempotent: rerunning it does not duplicate hooks or overwrite an already-customized statusline. If the user already has a statusline configured, the installer does not overwrite it; it prints clear instructions for combining the two.
3. A smoke test at the end of install confirms `cst list` runs successfully.

## 4. Feature list

| Feature | Description | User value | AI-assisted? |
|---|---|---|---|
| Auto session detection | Continuously discovers Claude Code sessions by scanning Claude Code's local project data so the user never has to manually register a session to see it. | Zero-effort inventory of every running session. | N |
| Auto title seeding | For a newly detected session with no user title, derive a short working title from the first user message in that session's transcript (fall back to project name if unavailable). | User sees meaningful titles immediately, even before they register the session. | **Y** (summarization of first user message into ≤60-char title) |
| User-owned metadata | Title, priority (high/medium/low), status (in_progress/blocked/waiting/done), note, tags — all editable by the user from any of the three surfaces. | A single source of truth for "what this window is about." | N |
| Unified registry across three surfaces | External CLI/TUI, in-Claude-Code slash commands, and the Claude Code statusline all read and write the same records. | No surface drift; edits anywhere appear everywhere. | N |
| Window focusing | Bring the terminal window that owns a chosen session to the front. Full support for iTerm2 and macOS Terminal.app; other terminal apps degrade gracefully. | The flagship "jump to that window" action. | N |
| Resume fallback | When focusing is impossible (window closed, terminal not scriptable), offer a one-command way to open a new terminal and resume that session via Claude Code's native resume. | User never hits a dead end. | N |
| Live vs idle indicator | In lists, mark each session as live (a Claude process is currently running on its tty) or idle. | Instantly see which windows are actually busy. | N |
| Priority + recency sort | Default list order is priority (high→low) then most-recently-active first. | The most important and most active work is on top. | N |
| Stale detection | Sessions idle longer than the configurable threshold (default 4h) that are still in an active status are flagged stale. Never auto-archived, never auto-deleted. | Surfaces forgotten work without punishing the user. | N |
| Stale triage wizard | Walk through stale sessions one at a time and decide keep / done / archive / skip. | Makes cleanup a deliberate 30-second ritual. | N |
| Manual done + archive | User explicitly completes or archives; both are reversible via direct edits until garbage-collected. | User stays in control; no surprise data loss. | N |
| Garbage collection | A user-invoked command deletes only registry records that have been archived for longer than 7 days. Never runs automatically. | Bounded disk usage without destructive automation. | N |
| Statusline summary | Claude Code's statusline shows a compact `📋 N pending · M stale` string (stale segment hidden when M=0), returning fast enough to not slow the chat UI. | Ambient awareness of workload inside every Claude Code session. | N |
| Slash command suite | Inside Claude Code: list tasks, register current session, set note / priority / status, mark done, focus another session by number or id. | Full control without leaving the chat. | N |
| External CLI | Outside Claude Code: list (with `--all` and `--stale` filters), watch TUI, focus, resume, register, set fields, done, archive, scan, review-stale, gc, statusline. | Full control without being inside any specific Claude Code session. | N |
| Watch TUI | Auto-refreshing interactive list with keyboard actions for focus, resume, edit, done, archive, quit. `--pin` variant opens in a dedicated window when the terminal supports it. | A heads-up display for heavy multi-session days. | N |
| Short-id prefix matching | Any command that takes a session id accepts a short prefix (≥6 chars). Ambiguous prefixes list candidates and refuse to act. | Fast typing, no accidental mis-selection. | N |
| Hook-driven activity tracking | Claude Code's session-start and user-prompt-submit hooks update the registry (initial terminal capture; last-activity timestamp). Hooks never block the user — if they fail, the Claude Code session continues normally. | Accurate freshness and window-binding data without user effort. | N |
| Corrupt file isolation | A damaged registry record is renamed out of the way (preserved for inspection), never deleted, and the rest of the registry keeps working. | Robustness: one bad file never breaks the tool. | N |
| Configurable stale threshold | Single-value user config to override the 4h default. | Power users tune freshness to their workflow. | N |
| Idempotent install / uninstall | Installer can be rerun safely; existing hook arrays get the entries appended once; existing statusline configs are not overwritten silently. | Safe to redeploy after upgrades. | N |
| Progress capture | Automatically record, for every session, the last user prompt (truncated to ~100 chars), the first line / ≤100 chars of the most recent assistant response, and a short "current task hint" derived from the last tool-use block in the transcript (e.g. `Running: pytest -q tests/`, `Editing: scripts/cst.py`; blank when no recent tool use). All three are scanner-owned and updated automatically — the user never enters them. No external AI model call is made; values come from extracting and truncating fields already present in the transcript. | Answers "what was I doing in that window?" without opening it. | **Y** (fields are summaries / first-line extractions of assistant-generated content) |
| Progress display in lists | `cst list` and `/tasks` render each session as a headline row plus up to two dim sub-rows showing the last user prompt and the current task hint. A `--compact` flag collapses each session back to a single line for scripting. The watch TUI's detail panel additionally shows the full last assistant summary. | Scannable at-a-glance context in every listing surface. | **Y** (derived from assistant output) |

## 5. Data model

All data is local to the user's machine. There is exactly one logical entity.

### Entity: Session record

One record per Claude Code session. Conceptual fields (not schema):

- **Identity**
  - Session id — the Claude Code session UUID; the registry never invents its own ids.
  - Short id — deterministic 6-character prefix used for human-friendly command input.
- **User-owned fields** (scanner is forbidden from overwriting these once the user has touched them)
  - Title — short human label.
  - Priority — one of `high`, `medium`, `low`.
  - Status — one of `in_progress`, `blocked`, `waiting`, `done`. (`stale` is a derived view-only state the scanner may assign to records that are still in an active status.)
  - Note — free-form text.
  - Tags — small set of short strings.
- **Context fields**
  - Working directory — absolute path where `claude` was launched.
  - Project name — short label for the project the session belongs to.
- **Progress fields** (scanner-owned; never user-editable; the scanner overwrites these freely on each pass. Populated by extracting and truncating transcript content — no external AI model call is made.)
  - Last user prompt — the most recent user message in the session, truncated to about 100 characters / one line. Also refreshed from Claude Code's user-prompt-submit signal; whichever signal is fresher wins.
  - Last assistant summary — the first line, or up to ~100 characters, of Claude's most recent response in the session.
  - Current task hint — a short derived label describing the most recent tool-use action in the transcript (e.g. `Running: pytest -q tests/`, `Editing: scripts/cst.py`). Null / empty when no tool-use block is near the tail.
- **Timing fields**
  - Created-at timestamp.
  - Last-activity-at timestamp — updated by hook on each user prompt submit and by the scanner from transcript file modification time.
- **Terminal binding**
  - Terminal app name (e.g. iTerm2, Terminal, Ghostty).
  - Window id, tab id — populated when the terminal app supports scripted window/tab identification; otherwise null.
  - tty device path — populated whenever knowable; used as a fallback identifier and for live-vs-idle detection.
- **Lifecycle flags**
  - `auto_detected` — true until the user has edited any user-owned field, then false.
  - `archived` — boolean; archived records are hidden from default listings.
  - `archived_at` — timestamp, used by `cst gc` to decide eligibility for deletion.

### Derived / computed state (not stored, recomputed at read time)

- **Stale** — true when the record is not archived, status is one of the active values, and `now - last_activity_at` exceeds the stale threshold.
- **Live** — true when a running `claude` process is currently attached to the record's tty.
- **Pending count** (for statusline) — number of non-archived records whose status is active.
- **Stale count** (for statusline) — number of records that satisfy the Stale condition above.

### Relationships

- One-to-one between a Session record and a Claude Code session UUID.
- One-to-many (logically): a project may own many sessions; a terminal app window/tab owns at most one session at a time.

### Concurrency and durability expectations

- Multiple processes (hooks, CLI, TUI, statusline) may read and write the registry simultaneously. Per-record writes must not clobber each other; a single corrupt record must not block reads of others.
- All data is local to the machine. No remote sync of any kind.

## 6. Screens / surfaces

There are exactly four user-visible surfaces, all reading the same registry.

### 6.1 CLI — `cst list` (and variants)

**Shows.** A list of non-archived sessions, sorted by priority then recency. Each session renders as a headline row plus up to two dim sub-rows:
- Headline row: short id, live-vs-idle dot (`●` live / `○` idle), priority badge, status (with `stale` called out), title, project name, last activity relative time.
- Sub-row 1 (dim): `⤷ <last user prompt>` — skipped if empty.
- Sub-row 2 (dim): `⚙ <current task hint>` — skipped when no tool-use is near the tail of the transcript.

A footer banner appears when any stale sessions exist, instructing the user to run `cst review-stale`.

**Variants.**
- `cst list --all` includes archived rows.
- `cst list --stale` shows only stale rows.
- `cst list --compact` collapses each session back to a single headline line (no sub-rows). Intended for scripting, CI, and pipelines.

**User can.** Copy a short id, pipe to other tools, decide their next action. This surface is read-only; mutations happen through other `cst` subcommands.

### 6.2 CLI — `cst watch` (TUI)

**Shows.** The same ranked list as `cst list` (including the progress sub-rows), auto-refreshing on a short interval (roughly every 2 seconds), with a highlighted current row. A detail panel for the highlighted row additionally shows the full last assistant summary, the full last user prompt, and the full current task hint (none of which are truncated in the panel view).

**User can.** Move the highlight with arrow keys, and from the highlighted row: focus the owning window (Enter), resume in a new window, edit note / priority / status, mark done, archive, or quit. A `--pin` invocation opens the TUI in a dedicated small terminal window when the terminal app supports scripted window creation.

### 6.3 Claude Code statusline

**Shows.** A single short line of text, e.g. `📋 3 pending · 1 stale  →  /tasks`. The stale segment is omitted when the count is zero. Must render fast enough not to lag the chat UI (target: tens of milliseconds). Progress fields (last user prompt / last assistant summary / current task hint) are intentionally NOT shown here due to space constraints.

**User can.** Read only — the statusline does not accept input. The arrow serves as a nudge to run `/tasks`.

### 6.4 Claude Code slash commands

- `/tasks` — prints a numbered list in chat: `[n] ●/○ priority  title  project  <time> ago`. Numbers are stable within the single response.
- `/task-register [title] [priority]` — registers / annotates the current session.
- `/task-note "<text>"` — updates the current session's note.
- `/task-priority high|medium|low` — changes the current session's priority.
- `/task-status in_progress|blocked|waiting` — changes the current session's status.
- `/task-done` — marks the current session done.
- `/task-focus <number|id>` — focuses another session's window, accepting either the number from the latest `/tasks` output or a short id prefix.

All slash commands operate on the registry and are indistinguishable in effect from the equivalent `cst` subcommand.

## 7. Non-goals

The following are explicitly out of scope and must not be implemented:

- Multi-machine sync, cloud storage, or any networked backend.
- A web UI.
- Inter-session dependency or blocking graphs.
- Desktop push notifications, sounds, or menu-bar icons.
- Automatic destructive cleanup of any kind. The only delete path is the user-invoked `cst gc`, and it only touches records archived for more than 7 days.
- Support for non-macOS platforms.
- Automatic focusing / window rearrangement that the user did not ask for.
- Overwriting user-edited fields from the scanner.
- Silent overwrite of an existing user statusline configuration.
- Any hook that can block or delay the user's Claude Code session on failure.

## 8. Definition of Done

All of the following must be observably true on a clean macOS machine after running the installer once.

### Installation and wiring
- [ ] Running the installer exactly once exposes a working `cst` command in the user's shell (or prints a clear, actionable message if PATH adjustment is needed).
- [ ] Running the installer a second time produces no duplicate hook entries, no duplicate slash commands, and no overwritten existing statusline.
- [ ] After install, `cst list` exits 0 even when the registry is empty.

### Detection and registration
- [ ] Opening a brand-new terminal window and running `claude` causes a new session record to appear in the registry within seconds, with project, cwd, tty, and terminal identifiers captured.
- [ ] A newly detected session with no user title has a non-empty title (derived from first user message or project name).
- [ ] Running `/task-register "X" high` inside that session updates the record and flips `auto_detected` to false; subsequent scans no longer overwrite the title, priority, status, note, or tags.

### Cross-surface parity
- [ ] Editing a session's priority via `/task-priority` is visible in the next `cst list` and `cst watch` refresh without any manual sync step.
- [ ] Editing a session via `cst set` is visible inside Claude Code via the next `/tasks` invocation.
- [ ] All three surfaces agree on which sessions are pending and which are stale at any moment.

### Finding the right window
- [ ] With two Claude Code sessions open in iTerm2, running `cst focus <id-of-the-other>` from one of them brings the other's window to the front.
- [ ] The same works for Terminal.app.
- [ ] When the owning window no longer exists, `cst focus` does NOT silently fail — it prints a clear message and suggests `cst resume <id>`.
- [ ] On an unsupported terminal app (e.g. Ghostty), `cst focus` reports the limitation and offers `cst resume <id>` instead of attempting an AppleScript that would error.
- [ ] `cst resume <id>` opens a new terminal window, cd's into the session's cwd, and starts Claude Code with resume against the correct session.

### Ranking and indicators
- [ ] `cst list` rows are ordered by priority (high → low) then by most-recent activity.
- [ ] Rows whose tty has a live `claude` process are marked live; others are marked idle.

### Stale handling
- [ ] A session whose last activity is set to 5 hours ago is shown as `stale` in `cst list` and `/tasks`.
- [ ] A footer banner appears in `cst list` whenever any stale sessions exist.
- [ ] `cst review-stale` presents each stale session in turn and accepts keep / done / archive / skip; choosing "skip" or "keep" never modifies the record.
- [ ] No code path ever transitions a session to archived or deletes a record without explicit user action.

### Statusline
- [ ] The Claude Code statusline displays `📋 N pending` whenever there is at least one pending session, and appends `· M stale` only when M > 0.
- [ ] The statusline call returns quickly enough not to visibly delay the Claude Code chat UI.
- [ ] The statusline command never performs network I/O or heavyweight parsing.

### Short-id ergonomics
- [ ] Any command that accepts a session id accepts a 6+ character prefix.
- [ ] An ambiguous prefix lists candidate sessions and exits non-zero without mutating anything.

### Lifecycle and garbage collection
- [ ] `cst done <id>` leaves the record visible in the default list as `done` until it is archived.
- [ ] `cst archive <id>` hides the record from the default list; `cst list --all` still shows it.
- [ ] `cst gc` deletes only records whose `archived_at` is older than 7 days and never touches any other record.
- [ ] No background process, hook, or scan ever deletes a record.

### Robustness
- [ ] Corrupting a single registry record on disk does not prevent `cst list`, the statusline, or the slash commands from functioning on the remaining records; the corrupt file is preserved under a renamed path.
- [ ] Failing hooks do not block the user's Claude Code session; the chat remains usable.
- [ ] All `cst` subcommands return a non-zero exit code and a human-readable error message on failure; no subcommand leaves the registry partially written.

### Configuration
- [ ] The user can override the stale threshold via a single local config value; setting it to 1 hour causes a session idle for 90 minutes to be flagged stale on the next list.

### Platform
- [ ] Everything works on macOS. The tool is permitted to refuse to run on non-macOS platforms with a clear message.

SPEC_READY: spec.md
