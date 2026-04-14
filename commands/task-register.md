---
description: Register the current Claude Code session with a title and optional priority
allowed-tools: Bash(cst:*)
argument-hint: "<title> [high|medium|low]"
---

Register the current session ($CLAUDE_SESSION_ID) in the claude-session-manager registry.

Arguments: $ARGUMENTS

Parse $ARGUMENTS: treat the final token as priority if it is one of `high`, `medium`, `low`; everything before it (or everything, if no priority token) is the title.

Then run:

!`cst set "$CLAUDE_SESSION_ID" --title "<title>" --priority "<priority|medium>"`

Report the resulting row from `cst list` for that session.
