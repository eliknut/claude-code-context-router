"""Resolve the project root, its memory store and its kit config.

The only module that turns paths into other paths. Everything else asks this.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def root() -> Path:
    """The project root.

    This file is at <root>/.claude/lib/rootpath.py, which makes the third parent the
    root by construction, and that wins whenever it holds CONTEXTS.md. Claude Code's
    CLAUDE_PROJECT_DIR is only the fallback (a root whose registry is missing): in a
    session started in a linked worktree (worktree mode, for example an Orca worktree)
    it names the worktree, and a CONTEXTS.md tracked in that repo must not replace the
    router's own registry. The env var also needs CONTEXTS.md to count.
    """
    own = Path(__file__).resolve().parents[2]
    if (own / "CONTEXTS.md").is_file():
        return own
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        candidate = Path(env).expanduser().resolve()
        if (candidate / "CONTEXTS.md").is_file():
            return candidate
    return own


def memory_dir(base: Path | None = None) -> Path:
    """The per-project memory store: ~/.claude/projects/<slug>/memory.

    Claude Code names the project folder after the absolute root path with every
    character that is not a letter or a digit replaced by a dash (re.sub(r"[^a-zA-Z0-9]",
    "-", path)), so /a/b becomes -a-b and /a_b/c.d becomes -a-b-c-d. Replacing only "/"
    is a different, narrower rule that happens to agree with this one for a root with
    no other punctuation (for example /home/alex/work), and diverges for any root
    containing an underscore, a dot or a space, which then gets a memory tree written
    at a path Claude Code will never read.

    Claude Code also caps the slug at 200 characters, past which it appends a hash
    suffix; that cap depends on Claude Code's own hash and cannot be reproduced with
    the standard library alone, so a root path longer than roughly 200 characters is
    out of scope here.
    """
    slug = re.sub(r"[^a-zA-Z0-9]", "-", str(base or root()))
    return Path.home() / ".claude" / "projects" / slug / "memory"


def config(base: Path | None = None) -> dict:
    """<root>/.claude/kit.json, or {} when absent, unreadable or malformed.

    A broken config must never break a session, so failures are reported on
    stderr and treated as an empty config.
    """
    path = (base or root()) / ".claude" / "kit.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"kit.json ignored: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def ctx_state() -> Path:
    """Where per-session context markers live, shared with set_context.sh.

    set_context.sh keeps its own literal since bash cannot import this module;
    the duplication is deliberate and visible.
    """
    return Path.home() / ".claude" / "ctx-state"
