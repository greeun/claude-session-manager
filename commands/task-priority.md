---
description: Set priority for the current Claude Code session
allowed-tools: Bash
argument-hint: "high|medium|low"
---

Use the Bash tool to run:

```
SID="${CLAUDE_SESSION_ID:-$(csm current)}" && csm set "$SID" --priority "$ARGUMENTS"
```

Report the result.
