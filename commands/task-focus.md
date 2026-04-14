---
description: Focus another Claude Code session's terminal window by short id prefix or row number
allowed-tools: Bash
argument-hint: "<short-id-prefix|row-number>"
---

Arguments: $ARGUMENTS

If $ARGUMENTS looks like a small integer (the row number from `/tasks`), first use the Bash tool to run `csm list --json`, parse JSON, and pick the Nth row's `session_id`. Otherwise treat $ARGUMENTS as a short-id prefix directly.

Use the Bash tool to run: `csm focus "<resolved-id-or-prefix>"`

If the command reports the window is missing, suggest running `csm resume "<same-id>"` instead.
