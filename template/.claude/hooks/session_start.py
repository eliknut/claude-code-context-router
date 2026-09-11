"""SessionStart hook: print the session id, report the context marker state, list recent handoffs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import rootpath  # noqa: E402
from handoff import find_recent_handoffs  # noqa: E402

ROOT = rootpath.root()
DAYS = 14
CTX_STATE = rootpath.ctx_state()
NONE_LINE = ("Active context: none. Resolve one from CONTEXTS.md before any context-bound work "
             "(see CLAUDE.md section 1).")
UNKNOWN_LINE = ("Active context: unknown to the hooks. If a context is already active in this conversation, "
                "rewrite its marker now (CLAUDE.md section 2) and do not re-ask which context this is.")


def main() -> int:
    try:
        data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    sid = data.get("session_id", "unknown")
    source = data.get("source", "")
    print(f"session_id: {sid}")
    marker = CTX_STATE / sid if sid else None
    name = ""
    if marker is not None:
        try:
            name = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
        except OSError:
            name = ""
    if name:
        print(f"Active context (from marker): {name}")
    elif source in ("resume", "compact", "fork"):
        # After a resume, compaction or fork, a context may already be active in the conversation
        # while the marker is missing or belongs to a different session id. This is exactly when
        # the "rewrite the marker, do not re-ask" line is the right response. After startup or
        # clear there is genuinely no context, so the other line is right.
        print(UNKNOWN_LINE)
    else:
        print(NONE_LINE)
    recent = find_recent_handoffs(ROOT, days=DAYS)
    if not recent:
        print(f"No HANDOFF.md updated in the last {DAYS} days.")
        return 0
    print(f"Handoffs updated in the last {DAYS} days, newest first:")
    for h in recent:
        ws = ", ".join(h["workstreams"]) if h["workstreams"] else "no workstreams listed"
        upd = h["updated"] or h["mtime"].strftime("%Y-%m-%d")
        print(f"- {h['context']} (updated {upd}): {ws}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
