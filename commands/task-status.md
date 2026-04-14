---
description: Set status for the current Claude Code session
allowed-tools: Bash(csm:*)
argument-hint: "in_progress|blocked|waiting|done"
---

!`csm set "$CLAUDE_SESSION_ID" --status "$ARGUMENTS"`
