import json
import re
import sys

payload = json.load(sys.stdin)
cmd = payload.get("tool_input", {}).get("command", "")

BLOCKED = [
    r"\brm\s+-rf\b",
    r"Remove-Item\b.*-Recurse",
    r"\bgit\s+push\b.*--force",
    r"\bdrop\s+database\b",
    r"\.env\b",  # CLAUDE.md: never print .env
]

for pattern in BLOCKED:
    if re.search(pattern, cmd, re.IGNORECASE):
        print(f"Blocked by safety_check: matches {pattern!r}", file=sys.stderr)
        sys.exit(2)

sys.exit(0)