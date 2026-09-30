"""Read and update per-context HANDOFF.md files."""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

HANDOFF_NAME = "HANDOFF.md"
RECENT_CAP = 10
SKIP_DIRS = {".git", "node_modules", ".terraform", ".terragrunt-cache", "dist", ".venv", "venv", "__pycache__", ".claude"}
TITLE_RE = re.compile(r"^# (.+?) handoff\s*$", re.M)
UPDATED_RE = re.compile(r"^updated:\s*(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)", re.M)
WORKSTREAM_RE = re.compile(r"^- \*\*(.+?)\*\*", re.M)


def _section(text: str, header: str) -> str:
    """Return the body of '## <header>' up to the next '## ' heading, or ''."""
    m = re.search(rf"^## {re.escape(header)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def find_recent_handoffs(root: Path, days: int, now: dt.datetime | None = None,
                         homes: list[str] | None = None) -> list[dict]:
    """Every HANDOFF.md under `root` updated within `days`, newest first.

    `homes` is the set of context home paths, relative to root. When given, a handoff
    counts only if it sits exactly at one of them. Snapshot and backup tooling mirrors
    handoffs into folders inside the root, and without this filter the same context is
    reported once per copy. The match is exact rather than a prefix on purpose: a mirror
    nested under a context's own home would otherwise still pass.
    """
    now = now or dt.datetime.now()
    cutoff = now - dt.timedelta(days=days)
    wanted = None if homes is None else {h.strip("/") for h in homes}
    found: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if HANDOFF_NAME not in filenames:
            continue
        path = Path(dirpath) / HANDOFF_NAME
        if wanted is not None:
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            if rel not in wanted:
                continue
        mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
        if mtime < cutoff:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = TITLE_RE.search(text)
        updated = UPDATED_RE.search(text)
        found.append({
            "context": title.group(1).strip() if title else path.parent.name,
            "path": path,
            "updated": updated.group(1) if updated else "",
            "workstreams": WORKSTREAM_RE.findall(_section(text, "Now")),
            "mtime": mtime,
        })
    found.sort(key=lambda h: h["mtime"], reverse=True)
    return found


def append_recent(path: Path, line: str, cap: int = RECENT_CAP) -> None:
    """Insert `line` as the first bullet of the Recent section, keeping every other line.

    Bullets are capped at `cap`, oldest first to be dropped. Non-bullet lines in the
    section (indented continuations, free-text notes) are re-emitted after the bullets
    in their original order, with runs of blank lines collapsed to one. A following
    '## ' section is left untouched.
    """
    text = Path(path).read_text(encoding="utf-8")
    header = "## Recent"
    if header not in text:
        text = text.rstrip("\n") + "\n\n" + header + "\n"
    head, _, tail = text.partition(header)
    tail_lines = tail.split("\n")
    # tail_lines[0] is the remainder of the header line (normally empty)
    rest = tail_lines[1:]
    end = len(rest)
    for i, candidate in enumerate(rest):
        if candidate.startswith("## "):
            end = i
            break
    body, after = rest[:end], rest[end:]
    bullets = [b for b in body if b.startswith("- ")]
    bullets = ([line.rstrip()] + bullets)[:cap]
    others: list[str] = []
    for other in body:
        if other.startswith("- "):
            continue
        if not other.strip() and (not others or not others[-1].strip()):
            continue
        others.append(other)
    while others and not others[-1].strip():
        others.pop()
    new_tail = "\n" + "\n".join(bullets) + "\n"
    if others:
        new_tail += "\n".join(others) + "\n"
    if any(a.strip() for a in after):
        new_tail += "\n" + "\n".join(after).rstrip("\n") + "\n"
    Path(path).write_text(head + header + new_tail, encoding="utf-8")
