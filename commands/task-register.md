---
description: Register the current Claude Code session with a title and optional priority
allowed-tools: Bash
argument-hint: "<title> [high|medium|low]"
---

Arguments: $ARGUMENTS

Parse $ARGUMENTS: if the final whitespace-separated token is one of `high`, `medium`, `low`, treat it as the priority and everything before as the title. Otherwise the entire $ARGUMENTS is the title and priority is `medium`.

Then use the Bash tool to run: `csm set "$CLAUDE_SESSION_ID" --title "<parsed-title>" --priority "<parsed-priority>"`

Report the resulting row from running `csm list` filtered to that session.
