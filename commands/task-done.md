---
description: Mark the current Claude Code session as done
allowed-tools: Bash
---

Use the Bash tool to run:

```
SID="${CLAUDE_SESSION_ID:-$(csm current)}" && csm done "$SID"
```

Report the result.
