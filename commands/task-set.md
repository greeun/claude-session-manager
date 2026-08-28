---
description: Set session fields (title, status, priority, note) for the current Claude Code session
allowed-tools: Bash
argument-hint: "<field> <value> — field: title|status|priority|note"
---

Arguments: $ARGUMENTS

Parse $ARGUMENTS: the first whitespace-separated token is the field name, the rest is the value.

Supported fields and their valid values:
- **title** `<text>` — set session title (with optional priority as last word: high|medium|low)
- **status** `in_progress|blocked|waiting|done`
- **priority** `high|medium|low`
- **note** `<text>`

For **title**: if the final token is `high`, `medium`, or `low`, treat it as priority and also set `--priority`. Otherwise priority defaults to `medium`.

Build and run the appropriate `csm set` command:

```
SID="${CLAUDE_SESSION_ID:-$(csm current)}"
csm set "$SID" --<field> "<value>"
```

For title with priority:
```
SID="${CLAUDE_SESSION_ID:-$(csm current)}"
csm set "$SID" --title "<parsed-title>" --priority "<parsed-priority>"
```

Report the result.
