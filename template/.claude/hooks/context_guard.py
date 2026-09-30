"""PreToolUse hook: keep a tool call inside the active context.

Three checks, all decided in lib/guard.py. The write check denies a write into another
context's home, handoff or memory, from Write, Edit or NotebookEdit and from the common
shell shapes in a Bash command (redirects, tee, cp, mv, sed -i, rm and the like). The cloud check rewrites a bare az, aws, terraform or
terragrunt call to run under the credential wrapper (cloudctx unless .claude/kit.json
names another, or sets cloud_wrapper to false to turn the check off). The subagent check
prepends the active context's block to a delegated subagent's prompt.

Everything unexpected is soft. The hook exits 0 and emits nothing, writing one line to
stderr, because a broken guard must never be able to stop work.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import guard  # noqa: E402
import registry  # noqa: E402
import rootpath  # noqa: E402


def active_context(session_id: str) -> str:
    if not session_id:
        return ""
    marker = rootpath.ctx_state() / session_id
    try:
        return marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    except OSError:
        # A marker that exists but cannot be read is a known state, not a malfunction.
        # Skipping the call would allow a bare cloud command; no active context denies
        # one. session_start.py reads it the same way.
        return ""


def emit(decision, tool_input: dict) -> None:
    """Print the hook's JSON, or nothing at all when there is no opinion."""
    if decision.action == "allow":
        return
    output: dict = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    specific = output["hookSpecificOutput"]
    if decision.action == "update":
        # Merged into a copy of the original, so a partial rewrite cannot drop a field
        # the tool needed.
        updated = dict(tool_input)
        updated.update(decision.updated_input or {})
        specific["updatedInput"] = updated
        if decision.reason:
            output["systemMessage"] = decision.reason
    else:
        specific["permissionDecision"] = decision.action
        specific["permissionDecisionReason"] = decision.reason
    print(json.dumps(output))


def main() -> int:
    try:
        data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        return 0
    try:
        root = rootpath.root()
        reg = registry.contexts(registry.load_registry(root / "CONTEXTS.md"))
        tool_input = data.get("tool_input") or {}
        decision = guard.decide(data.get("tool_name", ""), tool_input,
                                active_context(data.get("session_id", "")),
                                reg, root, rootpath.memory_dir(), rootpath.config(root),
                                cwd=data.get("cwd") or "")
        emit(decision, tool_input)
    except Exception as exc:  # noqa: BLE001
        print(f"context_guard skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
