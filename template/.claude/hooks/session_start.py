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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import rootpath  # noqa: E402
import registry  # noqa: E402
import guard  # noqa: E402
import worktree  # noqa: E402
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
# Same character rule as scripts/set_context.sh, so a marker this hook writes is one
# that script could have written.
SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")


def load_contexts() -> dict | None:
    """The registry, or None when it cannot be read.

    Failure is deliberately soft: the hook must not take the session down with it.
    Both callers below decide for themselves what None means for them.
    """
    try:
        return registry.contexts(registry.load_registry(ROOT / "CONTEXTS.md"))
    except (OSError, ValueError):
        return None


def context_homes(reg: dict | None) -> list[str] | None:
    """Home paths of every context, or None when the registry is unreadable.

    None means "do not filter", so an unreadable registry degrades to listing every
    handoff in the tree rather than to listing none of them.
    """
    if reg is None:
        return None
    return [e["home"].strip("/") for e in reg.values()
            if e.get("home") and e["home"] != "none"]


def print_registry_digest(reg: dict | None) -> None:
    """One line per context: the name, then its aliases.

    Failure is deliberately soft and loud. If the registry cannot be read the hook
    must not take the session down with it, but it must also not leave Claude
    believing there are no contexts, so it says to read the file directly instead.
    """
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


def worktree_context(cwd: str, reg: dict | None, root: Path) -> tuple[str, str]:
    """(line to print, context name) for a session whose cwd is in a linked worktree.

    Only a linked worktree whose own top folder is outside the root counts (the guard's
    rule): a worktree made inside the root is ordinary root content. The context is the
    one that owns the main checkout, by the guard's longest home/owns rule. ("", "")
    when cwd is not in such a worktree, the registry is unreadable, or anything fails;
    (no-owner line, "") when no context owns the repo.
    """
    if not cwd or reg is None:
        return "", ""
    try:
        hit = worktree.linked(cwd)
        if hit is None:
            return "", ""
        top, main = hit
        real_root = Path(root).resolve()
        if top == real_root or real_root in top.parents:
            return "", ""
        name = guard.owner_of(str(main), reg, root, rootpath.memory_dir(root))
        if not name:
            return f"worktree of {main}: no context owns it", ""
        try:
            shown = main.relative_to(real_root).as_posix()
        except ValueError:
            shown = str(main)
        return f"worktree of {shown} -> context {name}", name
    except Exception:  # the session must start whatever this lookup hits
        return "", ""


def write_marker(session_id: str, name: str) -> bool:
    """Write the marker as set_context.sh does. False, writing nothing, on an unsafe id."""
    for value in (session_id, name):
        if not SAFE_NAME.fullmatch(value) or value in (".", ".."):
            return False
    try:
        CTX_STATE.mkdir(parents=True, exist_ok=True)
        (CTX_STATE / session_id).write_text(name, encoding="utf-8")
    except OSError:
        return False
    return True


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
    reg = load_contexts()
    wt_line, wt_name = worktree_context(data.get("cwd") or "", reg, ROOT)
    if wt_line:
        print(wt_line)
    # Only on a fresh session (startup or clear) with no marker yet. session_end deletes the
    # marker at every exit, so after a resume, compaction or fork a missing marker does not
    # mean no context: the conversation may have switched context on purpose, and the
    # worktree must not undo that. Those sources get the unknown-context line below instead.
    if wt_name and not name and sid and source in ("startup", "clear") \
            and write_marker(sid, wt_name):
        name = wt_name
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
    print_registry_digest(reg)
    recent = find_recent_handoffs(ROOT, days=DAYS, homes=context_homes(reg))
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
