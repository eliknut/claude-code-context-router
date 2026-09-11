#!/bin/bash
# Usage: set_context.sh <context> <session_id>   Writes the marker the status line and the session-end hook read.
set -eu
[ $# -eq 2 ] || { echo "usage: set_context.sh <context> <session_id>" >&2; exit 2; }
case "$1$2" in *[!A-Za-z0-9._-]*) echo "invalid characters" >&2; exit 2;; esac
mkdir -p "$HOME/.claude/ctx-state"
printf '%s' "$1" > "$HOME/.claude/ctx-state/$2"
echo "context $1 marked for session $2"
