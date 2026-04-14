---
description: Focus another Claude Code session's terminal window by short id prefix or number
allowed-tools: Bash(cst:*)
argument-hint: "<short-id-prefix|row-number>"
---

If $ARGUMENTS is a small integer, first run `cst list --json` to resolve the Nth row's session_id.
Otherwise treat it as a short-id prefix directly.

Then run:

!`cst focus "$ARGUMENTS"`

If that fails with exit code indicating a missing window, suggest running `cst resume "$ARGUMENTS"` instead.
