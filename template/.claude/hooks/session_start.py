"""SessionStart hook: session id, context marker state, registry digest, recent handoffs.

The registry digest exists so CLAUDE.md does not have to import the whole of
CONTEXTS.md on every session. Resolving a context needs only the name and the
aliases; everything else in a block (the cloud scope, the home, the infrastructure
names, the standing rules) is needed for exactly one context, at activation, and is
read from the file then. This is the same split the memory layout already uses: an
index always, the individual files on demand.

It is generated from CONTEXTS.md on every run, never stored, so it cannot drift out
of step with the registry it summarises.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import rootpath  # noqa: E402
import registry  # noqa: E402
from handoff import find_recent_handoffs  # noqa: E402

ROOT = rootpath.root()
DAYS = 14
# Handoffs listed with their workstreams. The rest are still named, with their date,
# just without the detail: a stale context should stay visible without paying for a
# summary nobody reads. Newest first, so the cut always falls on the oldest.
DETAILED = 6
CTX_STATE = rootpath.ctx_state()
NONE_LINE = ("Active context: none. Resolve one from CONTEXTS.md before any context-bound work "
             "(see CLAUDE.md section 1).")
UNKNOWN_LINE = ("Active context: unknown to the hooks. If a context is already active in this conversation, "
                "rewrite its marker now (CLAUDE.md section 2) and do not re-ask which context this is.")


def print_registry_digest() -> None:
    """One line per context: the name, then its aliases.

    Failure is deliberately soft and loud. If the registry cannot be read the hook
    must not take the session down with it, but it must also not leave Claude
    believing there are no contexts, so it says to read the file directly instead.
    """
    try:
        reg = registry.contexts(registry.load_registry(ROOT / "CONTEXTS.md"))
    except (OSError, ValueError):
        reg = None
    if not reg:
        print("Registry digest unavailable: read CONTEXTS.md directly before resolving a context.")
        return
    print(f"Registry ({len(reg)} contexts), name then aliases. This is the whole list: resolve "
          f"against it, then read that one block from CONTEXTS.md at activation for its cloud "
          f"scope, home, IaC names and rules. For a folder path or a term not listed here, "
          f"search CONTEXTS.md.")
    for name, entry in reg.items():
        aliases = ", ".join(entry.get("aliases", []))
        print(f"  {name}: {aliases}" if aliases else f"  {name}")


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
    print_registry_digest()
    recent = find_recent_handoffs(ROOT, days=DAYS)
    if not recent:
        print(f"No HANDOFF.md updated in the last {DAYS} days.")
        return 0
    print(f"Handoffs updated in the last {DAYS} days, newest first:")
    for h in recent[:DETAILED]:
        ws = ", ".join(h["workstreams"]) if h["workstreams"] else "no workstreams listed"
        upd = h["updated"] or h["mtime"].strftime("%Y-%m-%d")
        print(f"- {h['context']} (updated {upd}): {ws}")
    rest = recent[DETAILED:]
    if rest:
        names = ", ".join(f"{h['context']} ({h['updated'] or h['mtime'].strftime('%Y-%m-%d')})"
                          for h in rest)
        print(f"Older, names only, open the file if you need one: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
