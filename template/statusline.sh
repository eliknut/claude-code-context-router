#!/bin/bash
# Status line: "ctx: <context> | <model> | <dir>"
input=$(cat)
IFS=$'\t' read -r sid model dir < <(printf '%s' "$input" | python3 -c '
import json, sys, os
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
sid = d.get("session_id", "") or "-"
model = (d.get("model") or {}).get("display_name", "") or "-"
cwd = (d.get("workspace") or {}).get("current_dir", "") or d.get("cwd", "") or "-"
print(sid, model, os.path.basename(cwd) or cwd, sep="\t")
')
ctx="none"
if [ -n "$sid" ] && [ "$sid" != "-" ] && [ -f "$HOME/.claude/ctx-state/$sid" ]; then
  ctx=$(cat "$HOME/.claude/ctx-state/$sid")
fi
printf 'ctx: %s | %s | %s\n' "$ctx" "$model" "$dir"
