---
description: Update the note on the current Claude Code session
allowed-tools: Bash
argument-hint: "<note text>"
---

Use the Bash tool to run:

```
SID="${CLAUDE_SESSION_ID:-$(csm current)}" && csm set "$SID" --note "$ARGUMENTS"
```

Report the result.
