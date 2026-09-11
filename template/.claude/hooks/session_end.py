"""SessionEnd hook: if a context marker exists for this session, append a trail line to its HANDOFF.md."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import rootpath  # noqa: E402
from handoff import HANDOFF_NAME, append_recent  # noqa: E402
from registry import load_registry  # noqa: E402

ROOT = rootpath.root()
STATE = rootpath.ctx_state()


def main() -> int:
    try:
        data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, OSError):
        return 0
    sid = data.get("session_id", "")
    reason = data.get("reason", "unknown")
    marker = STATE / sid if sid else None
    if not marker or not marker.is_file():
        return 0
    try:
        try:
            ctx = marker.read_text(encoding="utf-8").strip()
            reg = load_registry(ROOT / "CONTEXTS.md")
            home = reg.get(ctx, {}).get("home", "")
            if not home or home == "none":
                return 0
            path = ROOT / home / HANDOFF_NAME
            if path.is_file():
                stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
                append_recent(path, f"- {stamp}  session ended ({reason}), id {sid}")
            return 0
        except (OSError, KeyError, ValueError):
            return 0
    finally:
        marker.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
