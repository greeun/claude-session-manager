---
description: Set status for the current Claude Code session
allowed-tools: Bash
argument-hint: "in_progress|blocked|waiting|done"
---

Use the Bash tool to run:

```
SID="${CLAUDE_SESSION_ID:-$(csm current)}" && csm set "$SID" --status "$ARGUMENTS"
```

Report the result.
